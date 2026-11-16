"""Copies of plugin config templates in `$IDAUSR/cfg`.

A plugin declares templates in `configTemplates`. HCLI copies each template to its destination,
and compares the copy with the template in the installed plugin directory to detect user edits.
"""

from __future__ import annotations

import enum
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from hcli.lib.ida import get_ida_user_dir
from hcli.lib.ida.plugin import (
    CONFIG_TEMPLATE_ROOT,
    MAX_COMPONENT_DEPTH,
    ConfigTemplate,
    IDAMetadataDescriptor,
    get_component_name,
    get_inline_component_metadata,
    validate_config_templates_in_plugin_directory,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemplateFile:
    """A config template of an installed plugin, read into memory."""

    plugin: str
    destination: str
    destination_parts: tuple[str, ...]
    source: Path
    content: bytes

    @property
    def key(self) -> str:
        return self.destination.lower()

    @classmethod
    def from_config_template(cls, plugin: str, plugin_dir: Path, template: ConfigTemplate) -> TemplateFile:
        """Read the template file from the plugin directory.

        Raises:
            OSError: when the template file cannot be read.
        """
        source = plugin_dir / template.source
        return cls(
            plugin=plugin,
            destination=template.destination,
            destination_parts=template.destination_parts,
            source=source,
            content=source.read_bytes(),
        )


class DestinationKind(enum.Enum):
    MISSING = "missing"
    FILE = "file"
    UNMANAGED = "unmanaged"


@dataclass(frozen=True)
class DestinationState:
    """What is at a destination path: nothing, a regular file, or something HCLI does not touch."""

    kind: DestinationKind
    content: bytes = b""

    @classmethod
    def from_path(cls, path: Path) -> DestinationState:
        """Inspect the destination. Symlinks and anything other than a regular file are unmanaged.

        Raises:
            OSError: when the file exists but cannot be read.
        """
        if path.is_symlink():
            return cls(DestinationKind.UNMANAGED)
        if not path.exists():
            return cls(DestinationKind.MISSING)
        if not path.is_file():
            return cls(DestinationKind.UNMANAGED)
        return cls(DestinationKind.FILE, path.read_bytes())


class ActionKind(enum.Enum):
    WRITE = "write"
    REPLACE = "replace"
    DELETE = "delete"
    KEEP = "keep"
    KEEP_OUTDATED = "keep-outdated"
    SKIP_UNMANAGED = "skip-unmanaged"


@dataclass(frozen=True)
class ConfigTemplateAction:
    kind: ActionKind
    template: TemplateFile


def get_config_template_action(
    old: TemplateFile | None, new: TemplateFile | None, state: DestinationState
) -> ConfigTemplateAction | None:
    """Decide what to do with one destination.

    `old` is the template of the installed version, `new` the template of the version being installed.
    Install has no `old`, uninstall has no `new`.
    """
    template = new or old
    if template is None:
        return None

    if state.kind == DestinationKind.UNMANAGED:
        return ConfigTemplateAction(ActionKind.SKIP_UNMANAGED, template)

    if new is None:
        assert old is not None
        if state.kind == DestinationKind.MISSING:
            return None
        if state.content == old.content:
            return ConfigTemplateAction(ActionKind.DELETE, old)
        return ConfigTemplateAction(ActionKind.KEEP, old)

    if state.kind == DestinationKind.MISSING:
        return ConfigTemplateAction(ActionKind.WRITE, new)
    if state.content == new.content:
        return None
    if old is None:
        return ConfigTemplateAction(ActionKind.KEEP, new)
    if state.content == old.content:
        return ConfigTemplateAction(ActionKind.REPLACE, new)
    if old.content == new.content:
        return None
    return ConfigTemplateAction(ActionKind.KEEP_OUTDATED, new)


def get_config_template_actions(
    old: Iterable[TemplateFile], new: Iterable[TemplateFile], states: Mapping[str, DestinationState]
) -> list[ConfigTemplateAction]:
    """Decide what to do with each destination.

    Old and new templates pair up by destination. `states` maps each destination to what is there.
    A missing destination means a missing file.
    """
    old_by_destination = {template.destination: template for template in old}
    new_by_destination = {template.destination: template for template in new}
    missing = DestinationState(DestinationKind.MISSING)

    actions: list[ConfigTemplateAction] = []
    for destination in sorted(old_by_destination.keys() | new_by_destination.keys()):
        action = get_config_template_action(
            old_by_destination.get(destination),
            new_by_destination.get(destination),
            states.get(destination, missing),
        )
        if action is not None:
            actions.append(action)
    return actions


def get_config_template_path(ida_user_dir: Path, template: TemplateFile) -> Path:
    return ida_user_dir.joinpath(*template.destination_parts)


def get_destination_states(ida_user_dir: Path, templates: Iterable[TemplateFile]) -> dict[str, DestinationState]:
    """Inspect the destination of each template.

    Raises:
        OSError: when a destination exists but cannot be read.
    """
    return {
        template.destination: DestinationState.from_path(get_config_template_path(ida_user_dir, template))
        for template in templates
    }


def write_file_atomically(path: Path, content: bytes) -> None:
    """Write a file through a temporary file in the same directory, so readers never see a partial file.

    Raises:
        OSError: when the directory cannot be created or the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        # mkstemp creates the file with mode 0600. Apply the umask instead, as open() does for a new file.
        umask = os.umask(0)
        os.umask(umask)
        temp_path.chmod(0o666 & ~umask)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def remove_empty_parent_directories(path: Path, ida_user_dir: Path) -> None:
    """Remove the empty directories between `path` and `$IDAUSR/cfg`, but never `$IDAUSR/cfg` itself."""
    root = ida_user_dir / CONFIG_TEMPLATE_ROOT
    directory = path.parent
    while directory != root and root in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        logger.debug("removed empty directory %s", directory)
        directory = directory.parent


class ConfigTemplateError(Exception):
    """One or more config template copies could not be written or deleted."""

    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("; ".join(failures))


def render_config_template_action(action: ConfigTemplateAction, path: Path) -> str:
    display = escape(str(path))
    match action.kind:
        case ActionKind.WRITE:
            return f"wrote {display}"
        case ActionKind.REPLACE:
            return f"replaced {display}"
        case ActionKind.DELETE:
            return f"deleted {display}"
        case ActionKind.KEEP:
            return f"kept {display}, which differs from the template of {escape(action.template.plugin)}"
        case ActionKind.KEEP_OUTDATED:
            return (
                f"[yellow]kept {display}, which you edited. "
                f"The template of {escape(action.template.plugin)} changed: {escape(str(action.template.source))}[/yellow]"
            )
        case ActionKind.SKIP_UNMANAGED:
            return f"skipped {display}, which is not a regular file"


def apply_config_template_actions(
    actions: Iterable[ConfigTemplateAction], ida_user_dir: Path, console: Console
) -> None:
    """Write and delete the copies, and print one line for each file.

    Every action is attempted, also after a failure.

    Raises:
        ConfigTemplateError: when a write or delete failed.
    """
    failures: list[str] = []
    for action in actions:
        path = get_config_template_path(ida_user_dir, action.template)
        try:
            if action.kind in (ActionKind.WRITE, ActionKind.REPLACE):
                write_file_atomically(path, action.template.content)
            elif action.kind == ActionKind.DELETE:
                path.unlink()
                remove_empty_parent_directories(path, ida_user_dir)
        except OSError as e:
            failures.append(f"failed to {action.kind.value} {path}: {e}")
            continue
        console.print(render_config_template_action(action, path), soft_wrap=True)

    if failures:
        raise ConfigTemplateError(failures)


def get_component_metadatas_from_directory(
    plugin_dir: Path, metadata: IDAMetadataDescriptor, *, _depth: int = 0
) -> list[tuple[Path, IDAMetadataDescriptor]]:
    """Get the directory and metadata of each component of an installed plugin, at any depth.

    A component without `ida-plugin.json` uses the expanded metadata in the `components` field of its suite.

    Raises:
        ValueError: when a component has no metadata, a manifest is invalid, or the nesting is too deep.
        OSError: when a manifest cannot be read.
    """
    if _depth >= MAX_COMPONENT_DEPTH:
        raise ValueError(f"component nesting exceeds maximum depth ({MAX_COMPONENT_DEPTH})")

    result: list[tuple[Path, IDAMetadataDescriptor]] = []
    for entry in metadata.plugin.components:
        name = get_component_name(entry)
        component_dir = plugin_dir / name
        metadata_file = component_dir / "ida-plugin.json"
        if metadata_file.is_file():
            component = IDAMetadataDescriptor.model_validate_json(metadata_file.read_text(encoding="utf-8"))
        else:
            inline = get_inline_component_metadata(metadata, name)
            if inline is None:
                raise ValueError(f"ida-plugin.json not found in {component_dir}")
            component = inline
        if component.plugin.name != name:
            raise ValueError(f"component directory '{name}' contains plugin '{component.plugin.name}' (name mismatch)")
        result.append((component_dir, component))
        result.extend(get_component_metadatas_from_directory(component_dir, component, _depth=_depth + 1))
    return result


def get_metadatas_from_directory(plugin_dir: Path) -> list[tuple[Path, IDAMetadataDescriptor]]:
    """Get the directory and metadata of an installed plugin and of each of its components.

    Raises:
        ValueError: when a manifest is missing or invalid.
        OSError: when a manifest cannot be read.
    """
    metadata_file = plugin_dir / "ida-plugin.json"
    if not metadata_file.is_file():
        raise ValueError(f"ida-plugin.json not found in {plugin_dir}")
    metadata = IDAMetadataDescriptor.model_validate_json(metadata_file.read_text(encoding="utf-8"))
    return [(plugin_dir, metadata), *get_component_metadatas_from_directory(plugin_dir, metadata)]


def get_config_template_files(plugin_dir: Path) -> list[TemplateFile]:
    """Validate and read the config templates of an installed plugin and of its components.

    Raises:
        ValueError: when a manifest is invalid, a destination belongs to another plugin,
            or two manifests declare the same destination.
        OSError: when a template file cannot be read.
    """
    templates: dict[str, TemplateFile] = {}
    for path, metadata in get_metadatas_from_directory(plugin_dir):
        validate_config_templates_in_plugin_directory(path, metadata)
        for config_template in metadata.plugin.config_templates:
            template = TemplateFile.from_config_template(metadata.plugin.name, path, config_template)
            if template.key in templates:
                raise ValueError(f"config template destination declared twice: '{template.destination}'")
            templates[template.key] = template

    return list(templates.values())


def read_config_templates_before_change(plugin_dir: Path) -> list[TemplateFile]:
    """Read the templates of an installed plugin before an upgrade or uninstall removes them.

    A plugin whose templates cannot be read has no templates, so HCLI does not touch its copies.
    """
    try:
        return get_config_template_files(plugin_dir)
    except (ValueError, OSError) as e:
        logger.debug("cannot read config templates of %s: %s", plugin_dir, e)
        return []


def validate_config_template_files_unclaimed(templates: Iterable[TemplateFile], claims: Mapping[str, str]) -> None:
    """Check that no other installed plugin declares a destination.

    Raises:
        ValueError: when another installed plugin declares a destination.
    """
    for template in templates:
        if template.key in claims:
            raise ValueError(
                f"config template destination '{template.destination}' of '{template.plugin}' "
                f"is already declared by installed plugin '{claims[template.key]}'"
            )


def install_config_templates(plugin_dir: Path, other_plugin_dirs: Iterable[Path], console: Console) -> None:
    """Copy the templates of a freshly installed plugin.

    `other_plugin_dirs` are the directories of the other installed plugins.

    Raises:
        ValueError: when a manifest is invalid or a destination belongs to another plugin.
        OSError: when a template or a destination cannot be read.
        ConfigTemplateError: when a copy cannot be written.
    """
    new = get_config_template_files(plugin_dir)
    if not new:
        return
    validate_config_template_files_unclaimed(new, get_config_template_claims(other_plugin_dirs))
    ida_user_dir = get_ida_user_dir()
    actions = get_config_template_actions([], new, get_destination_states(ida_user_dir, new))
    apply_config_template_actions(actions, ida_user_dir, console)


def upgrade_config_templates(
    old: list[TemplateFile], plugin_dir: Path, other_plugin_dirs: Iterable[Path], console: Console
) -> None:
    """Update the copies after the files of the new version are in place.

    `old` comes from `read_config_templates_before_change`.
    `other_plugin_dirs` are the directories of the other installed plugins.

    Copies of templates that the new version does not declare at the same destination are handled first,
    so that a destination that changes only in case moves to the new name.

    Raises:
        ValueError: when a manifest is invalid or a destination belongs to another plugin.
        OSError: when a template or a destination cannot be read.
        ConfigTemplateError: when a copy cannot be written or deleted.
    """
    new = get_config_template_files(plugin_dir)
    if not old and not new:
        return
    if new:
        validate_config_template_files_unclaimed(new, get_config_template_claims(other_plugin_dirs))

    ida_user_dir = get_ida_user_dir()
    new_destinations = {template.destination for template in new}
    removed = [template for template in old if template.destination not in new_destinations]
    remaining = [template for template in old if template.destination in new_destinations]

    failures: list[str] = []
    try:
        actions = get_config_template_actions(removed, [], get_destination_states(ida_user_dir, removed))
        apply_config_template_actions(actions, ida_user_dir, console)
    except ConfigTemplateError as e:
        failures.extend(e.failures)

    try:
        actions = get_config_template_actions(remaining, new, get_destination_states(ida_user_dir, new))
        apply_config_template_actions(actions, ida_user_dir, console)
    except ConfigTemplateError as e:
        failures.extend(e.failures)

    if failures:
        raise ConfigTemplateError(failures)


def uninstall_config_templates(old: list[TemplateFile], console: Console) -> None:
    """Delete the copies that still match the templates, after the plugin directory is removed.

    `old` comes from `read_config_templates_before_change`.

    Raises:
        OSError: when a destination cannot be read.
        ConfigTemplateError: when a copy cannot be deleted.
    """
    if not old:
        return
    ida_user_dir = get_ida_user_dir()
    actions = get_config_template_actions(old, [], get_destination_states(ida_user_dir, old))
    apply_config_template_actions(actions, ida_user_dir, console)


def get_config_template_claims(plugin_dirs: Iterable[Path]) -> dict[str, str]:
    """Get the plugin that declares each destination, by destination key, from installed plugin directories.

    Plugins whose manifests cannot be read declare nothing.
    """
    claims: dict[str, str] = {}
    for plugin_dir in plugin_dirs:
        try:
            metadatas = get_metadatas_from_directory(plugin_dir)
        except (ValueError, OSError) as e:
            logger.debug("cannot read config templates of %s: %s", plugin_dir, e)
            continue
        for _, metadata in metadatas:
            for template in metadata.plugin.config_templates:
                claims.setdefault(template.destination.lower(), metadata.plugin.name)
    return claims


def validate_config_template_destinations(
    metadatas: Iterable[IDAMetadataDescriptor], claims: Mapping[str, str]
) -> None:
    """Check that the manifests of one plugin and its components declare each destination once,
    and that no other installed plugin declares it.

    `claims` comes from `get_config_template_claims` for the installed plugins, without the plugin itself.

    Raises:
        ValueError: when a destination is declared twice or already declared by an installed plugin.
    """
    seen: dict[str, str] = {}
    for metadata in metadatas:
        name = metadata.plugin.name
        for template in metadata.plugin.config_templates:
            key = template.destination.lower()
            if key in seen:
                raise ValueError(
                    f"config template destination '{template.destination}' is declared by both "
                    f"'{seen[key]}' and '{name}'"
                )
            seen[key] = name
            if key in claims:
                raise ValueError(
                    f"config template destination '{template.destination}' of '{name}' "
                    f"is already declared by installed plugin '{claims[key]}'"
                )
