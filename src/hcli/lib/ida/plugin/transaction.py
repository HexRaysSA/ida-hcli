"""Journaled mutation of the plugins directory, editable ``.pth`` files, and settings.

Every mutating step performs its change and records the compensation needed to
undo it in one call. Rollback replays the journal in reverse. Replaced plugin
directories are checkpointed in the trash area and only deleted on commit, so a
failure at any later step restores the exact prior content.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
import weakref
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hcli.lib.ida import PluginConfig, get_ida_config, set_ida_config
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.exceptions import PluginInUseError
from hcli.lib.ida.plugin.install import get_trash_directory, is_file_in_use_error

logger = logging.getLogger(__name__)

RECOVERY_DIR_PREFIX = "recovery-"

_open_transactions: weakref.WeakSet[InstallTransaction] = weakref.WeakSet()


def is_transaction_active() -> bool:
    return any(not txn.finished for txn in _open_transactions)


@dataclass(frozen=True)
class PublishedDirectory:
    path: Path


@dataclass(frozen=True)
class ReplacedDirectory:
    """``path`` was moved to ``checkpoint_path``; whatever now sits at ``path`` is new."""

    path: Path
    checkpoint_path: Path


@dataclass(frozen=True)
class PthFileChange:
    path: Path
    previous_content: bytes | None


@dataclass(frozen=True)
class ConfigKeyChange:
    plugin_name: str
    key: str
    previous_value: str | bool | None
    existed: bool


JournalEntry = PublishedDirectory | ReplacedDirectory | PthFileChange | ConfigKeyChange


@dataclass(frozen=True)
class Savepoint:
    position: int


class PreconditionChangedError(Exception):
    """The on-disk or configured state differs from what the plan observed."""


class RollbackError(Exception):
    """Rollback could not fully restore the prior state.

    ``original`` is the failure that triggered the rollback, ``failures`` are
    the cleanup errors, and ``retained_paths`` are checkpoints preserved under
    the recovery directory for manual repair.
    """

    def __init__(self, original: BaseException | None, failures: Sequence[Exception], retained_paths: Sequence[Path]):
        self.original = original
        self.failures = list(failures)
        self.retained_paths = list(retained_paths)
        details = "; ".join(str(f) for f in self.failures)
        retained = ", ".join(str(p) for p in self.retained_paths)
        message = f"rollback incomplete: {details}"
        if retained:
            message += f". Prior content retained at: {retained}"
        super().__init__(message)


@dataclass(eq=False)
class InstallTransaction:
    """Journal of mutations with reverse-order rollback and savepoints."""

    plugins_dir: Path
    journal: list[JournalEntry] = field(default_factory=list)
    staging: list[Path] = field(default_factory=list)
    finished: bool = False

    def __post_init__(self) -> None:
        _open_transactions.add(self)

    @property
    def trash_dir(self) -> Path:
        trash = get_trash_directory(self.plugins_dir)
        trash.mkdir(parents=True, exist_ok=True)
        return trash

    def _check_open(self) -> None:
        if self.finished:
            raise RuntimeError("transaction already finished")

    def make_staging_directory(self, label: str) -> Path:
        """A fresh directory in the trash area that rollback removes if it is left behind."""
        self._check_open()
        path = self.trash_dir / f"{label}.staging-{uuid.uuid4().hex[:8]}"
        path.mkdir()
        self.staging.append(path)
        return path

    def publish_directory(self, staged: Path, destination: Path) -> None:
        """Rename ``staged`` to ``destination``, checkpointing any existing destination first.

        Raises:
            PluginInUseError: when the existing destination cannot be moved because files are in use.
        """
        self._check_open()
        replaced = self._checkpoint(destination)
        os.rename(staged, destination)
        if staged in self.staging:
            self.staging.remove(staged)
        if not replaced:
            self.journal.append(PublishedDirectory(destination))

    def replace_directory(self, staged: Path, destination: Path) -> None:
        """Replace an existing ``destination``; fails when nothing is there to replace.

        Raises:
            PreconditionChangedError: when ``destination`` does not exist.
            PluginInUseError: when the existing destination cannot be moved because files are in use.
        """
        self._check_open()
        if not (destination.is_symlink() or destination.exists()):
            raise PreconditionChangedError(f"expected an installed plugin at {destination} but found nothing")
        self.publish_directory(staged, destination)

    def link_directory(self, source: Path, destination: Path) -> None:
        """Create a symlink at ``destination``, checkpointing any existing entry first."""
        self._check_open()
        replaced = self._checkpoint(destination)
        try:
            destination.symlink_to(source, target_is_directory=True)
        except OSError as e:
            raise ValueError(
                f"Failed to create symlink {destination} -> {source}: {e}. "
                "On Windows, symlink creation requires Developer Mode or administrator privileges."
            ) from e
        if not replaced:
            self.journal.append(PublishedDirectory(destination))

    def _checkpoint(self, destination: Path) -> bool:
        """Move an existing ``destination`` aside and journal the move; false when nothing was there.

        The journal entry is written before the caller fills ``destination``, so
        a failure between the two still restores the prior content on rollback.
        """
        if not (destination.is_symlink() or destination.exists()):
            return False
        checkpoint = self.trash_dir / f"{destination.name}.checkpoint-{uuid.uuid4().hex[:8]}"
        try:
            os.rename(destination, checkpoint)
        except OSError as e:
            if is_file_in_use_error(e):
                raise PluginInUseError(destination.name, destination) from e
            raise
        self.journal.append(ReplacedDirectory(destination, checkpoint))
        return True

    def write_pth(self, path: Path, content: str) -> None:
        self._check_open()
        previous = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        self.journal.append(PthFileChange(path, previous))

    def remove_pth(self, path: Path) -> None:
        self._check_open()
        if not path.exists():
            return
        previous = path.read_bytes()
        path.unlink()
        self.journal.append(PthFileChange(path, previous))

    def set_config_key(
        self,
        plugin_name: str,
        key: str,
        value: str | bool,
        metadata: IDAMetadataDescriptor,
        *,
        expected_existing: bool | None = None,
        expected_value: str | bool | None = None,
    ) -> None:
        """Validate and persist one setting, journaling the prior value.

        When ``expected_existing`` is given, the current stored state must match
        it (and ``expected_value`` when the key exists) or nothing is written.

        Raises:
            PreconditionChangedError: when the stored value changed since planning.
            ValueError: when the value does not fit the setting descriptor.
        """
        self._check_open()
        descriptor = metadata.plugin.get_setting(key)
        if (descriptor.type == "string" and not isinstance(value, str)) or (
            descriptor.type == "boolean" and not isinstance(value, bool)
        ):
            raise ValueError(f"mismatching settings types: {plugin_name}: {key}: {descriptor.type}")
        descriptor.validate_value(value)

        config = get_ida_config()
        plugin_config = config.plugins.get(plugin_name)
        existed = plugin_config is not None and key in plugin_config.settings
        previous = plugin_config.settings[key] if existed and plugin_config is not None else None
        changed = existed != expected_existing or (existed and previous != expected_value)
        if expected_existing is not None and changed:
            raise PreconditionChangedError(f"setting {plugin_name}.{key} changed since planning; rerun the command")
        if existed and previous == value:
            return
        if plugin_config is None:
            plugin_config = PluginConfig()
        plugin_config.settings[key] = value
        config.plugins[plugin_name] = plugin_config
        set_ida_config(config)
        self.journal.append(ConfigKeyChange(plugin_name, key, previous, existed))

    def savepoint(self) -> Savepoint:
        self._check_open()
        return Savepoint(len(self.journal))

    def rollback_to(self, savepoint: Savepoint) -> None:
        """Undo entries recorded after ``savepoint``; the earlier journal stays intact.

        Raises:
            RollbackError: when some entry could not be restored.
        """
        self._check_open()
        self._undo(savepoint.position, original=None)

    def rollback(self, original: BaseException | None = None) -> None:
        """Undo every journal entry and remove leftover staging directories.

        Raises:
            RollbackError: when some entry could not be restored; ``original`` is attached.
        """
        self._check_open()
        try:
            self._undo(0, original=original)
        finally:
            self._finish()

    def commit(self) -> None:
        """Discard checkpoints and leftover staging; the journal becomes permanent."""
        self._check_open()
        for entry in self.journal:
            if isinstance(entry, ReplacedDirectory):
                _discard_path(entry.checkpoint_path)
        self.journal.clear()
        self._finish()

    def _finish(self) -> None:
        for staged in self.staging:
            _discard_path(staged)
        self.staging.clear()
        self.finished = True
        _open_transactions.discard(self)

    def _undo(self, position: int, *, original: BaseException | None) -> None:
        """Undo journal entries above ``position``.

        A failing step is recorded and its checkpoint kept under the recovery
        directory. An interrupt (``KeyboardInterrupt``, ``SystemExit``) stops
        the rollback; every checkpoint not yet restored, and every published
        directory not yet removed, is moved to the recovery directory before
        the interrupt propagates, so a later trash sweep cannot delete prior
        content and no half-applied plugin stays in the plugins directory.
        """
        failures: list[Exception] = []
        retained: list[Path] = []
        recovery: Path | None = None
        while len(self.journal) > position:
            entry = self.journal.pop()
            try:
                self._undo_entry(entry)
            except Exception as e:
                logger.debug("rollback step failed for %s: %s", entry, e)
                failures.append(e)
                if isinstance(entry, ReplacedDirectory) and entry.checkpoint_path.exists():
                    recovery = recovery or self._make_recovery_directory()
                    retained.append(self._retain(entry.checkpoint_path, entry.path.name, recovery))
            except BaseException:
                pending = [entry, *self.journal[position:]]
                del self.journal[position:]
                for pending_entry in pending:
                    if isinstance(pending_entry, ReplacedDirectory) and pending_entry.checkpoint_path.exists():
                        recovery = recovery or self._make_recovery_directory()
                        retained.append(self._retain(pending_entry.checkpoint_path, pending_entry.path.name, recovery))
                    elif isinstance(pending_entry, PublishedDirectory) and (
                        pending_entry.path.is_symlink() or pending_entry.path.exists()
                    ):
                        recovery = recovery or self._make_recovery_directory()
                        retained.append(self._retain(pending_entry.path, pending_entry.path.name, recovery))
                if retained:
                    logger.warning(
                        "rollback interrupted; unrestored content retained at: %s", ", ".join(str(p) for p in retained)
                    )
                raise
        if failures:
            raise RollbackError(original, failures, retained) from original

    def _undo_entry(self, entry: JournalEntry) -> None:
        if isinstance(entry, PublishedDirectory):
            _remove_path(entry.path)
        elif isinstance(entry, ReplacedDirectory):
            if entry.path.is_symlink() or entry.path.exists():
                _remove_path(entry.path)
            os.rename(entry.checkpoint_path, entry.path)
        elif isinstance(entry, PthFileChange):
            if entry.previous_content is None:
                if entry.path.exists():
                    entry.path.unlink()
            else:
                entry.path.parent.mkdir(parents=True, exist_ok=True)
                entry.path.write_bytes(entry.previous_content)
        else:
            config = get_ida_config()
            plugin_config = config.plugins.get(entry.plugin_name)
            if entry.existed:
                assert entry.previous_value is not None
                if plugin_config is None:
                    plugin_config = PluginConfig()
                plugin_config.settings[entry.key] = entry.previous_value
                config.plugins[entry.plugin_name] = plugin_config
            elif plugin_config is not None:
                plugin_config.settings.pop(entry.key, None)
                if not plugin_config.settings and not (plugin_config.model_extra or {}):
                    del config.plugins[entry.plugin_name]
            set_ida_config(config)

    def _make_recovery_directory(self) -> Path:
        recovery = self.trash_dir / f"{RECOVERY_DIR_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        recovery.mkdir(parents=True, exist_ok=True)
        return recovery

    def _retain(self, checkpoint: Path, name: str, recovery: Path) -> Path:
        target = recovery / name
        if target.exists() or target.is_symlink():
            target = recovery / f"{name}-{uuid.uuid4().hex[:6]}"
        try:
            os.rename(checkpoint, target)
        except OSError as e:
            logger.debug("could not move checkpoint %s into %s: %s", checkpoint, recovery, e)
            return checkpoint
        return target


def _discard_path(path: Path) -> None:
    """Remove leftover transaction state, leaving it for a later sweep if that fails."""
    try:
        _remove_path(path)
    except OSError as e:
        logger.warning("could not remove %s: %s; it will be removed by a later sweep", path, e)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
