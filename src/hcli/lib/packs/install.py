"""FLIRT pack installs into an IDA signature folder, and the ``sync`` plan derived from them.

A FLIRT pack archive holds only ``sigs/<sha256>.sig`` files. Each pack is
installed into its own folder under a signature folder (``--sig-dir``,
by default IDA's user signature folder ``$IDAUSR/sig``, see ``resolve_sig_dir``)::

    <sig-dir>/<proc>/hcli-packs/<component>/<platform-group-arch>/
        <sha256>.sig ...
        .hcli-pack.json        the install manifest, written last

IDA looks for signatures under ``sig/<proc>/`` and finds these by relative
name, e.g. ``hcli-packs/msvc/windows-all-x86_64/<sha256>``.

The manifest is the install marker and the only install state: a folder
without a readable manifest, or holding another number of signatures than
the manifest records, counts as not installed. A pack is extracted into a
work folder, ``<sig-dir>/.hcli-work/<random>.new`` (same filesystem as the
target, outside every ``<proc>/`` folder IDA reads), gets its manifest,
is flushed to disk, then is swapped into place (the old folder renamed
aside into the work folder, the new one renamed in, the old one removed:
directory renames never replace an existing folder on Windows), so an
interrupted install never leaves a half-populated folder that looks
installed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import tarfile
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import IO, Final

import zstandard
from pydantic import BaseModel, ConfigDict, ValidationError

from hcli.lib.ida import get_ida_user_dir
from hcli.lib.packs.errors import (
    IntegrityError,
    InvalidReleaseError,
    LocalIOError,
    UnsupportedArchError,
)
from hcli.lib.packs.models import PackFormat, ReleasePack, is_safe_segment
from hcli.lib.packs.state import utc_now

INSTALL_SUBDIR: Final = "hcli-packs"
MANIFEST_NAME: Final = ".hcli-pack.json"
MANIFEST_SCHEMA_VERSION: Final = 1
# Where installs are staged and set aside: ``<sig-dir>/.hcli-work/``.
WORK_SUBDIR: Final = ".hcli-work"
# IDA's signature folder per pack architecture (``sig/<proc>/``); any other arch cannot be installed.
PROC_DIRS: Final = {
    "arm": "arm",
    "arm64": "arm",
    "arm64e": "arm",
    "arm64ec": "arm",
    "arm64_32": "arm",
    "x86": "pc",
    "x86_64": "pc",
}
SIG_SUFFIX: Final = ".sig"
# Exactly what the pack builder writes: ``sigs/<sha256>.sig``, no directory entries.
_SIG_MEMBER: Final = re.compile(r"^sigs/([0-9a-f]{64}\.sig)$")
_WORK_NAME: Final = re.compile(r"^[0-9a-f]{16}\.(new|old)$")
_STALE_WORK_AGE: Final = 24 * 60 * 60
_CHUNK: Final = 1024 * 1024
_BAD_ARCHIVE_HINT: Final = "the pack passed sha256 verification, so it was published this way; report it to Hex-Rays"


def resolve_sig_dir(sig_dir: Path | None = None) -> Path:
    """``--sig-dir`` if given, else IDA's user signature folder: ``sig`` in hcli's IDA user folder.

    That folder is the one plugins install into (``$HCLI_IDAUSR``, else ``$IDAUSR``, else the
    platform default), so ``HCLI_IDAUSR`` moves both.
    """
    return sig_dir.expanduser() if sig_dir is not None else get_ida_user_dir() / "sig"


def is_supported(pack: ReleasePack) -> bool:
    """Whether hcli knows IDA's signature folder for the pack's architecture."""
    return pack.arch in PROC_DIRS


def proc_dir(pack: ReleasePack) -> str:
    """IDA's signature subfolder for the pack's architecture (``pc``, ``arm``); UnsupportedArchError otherwise."""
    proc = PROC_DIRS.get(pack.arch)
    if proc is None:
        supported = ", ".join(sorted(PROC_DIRS))
        raise UnsupportedArchError(
            f"FLIRT pack {pack.address} is for arch {pack.arch!r}, which hcli cannot install (supported: {supported}).",
            "upgrade hcli (hcli update); meanwhile select other packs, e.g. with --arch",
        )
    return proc


def pack_folder_name(pack: ReleasePack) -> str:
    """``platform-group-arch``, the pack's folder name (its address without the component)."""
    name = f"{pack.platform}-{pack.group}-{pack.arch}"
    if not is_safe_segment(name):
        raise InvalidReleaseError(
            f"Refusing to install {pack.address}: {name!r} is not a safe folder name.",
            "the Release names an unsafe pack; report it to Hex-Rays",
        )
    return name


def install_dir(root: Path, pack: ReleasePack) -> Path:
    """``<root>/<proc>/hcli-packs/<component>/<platform-group-arch>``."""
    return root / proc_dir(pack) / INSTALL_SUBDIR / pack.component / pack_folder_name(pack)


def sig_reference(pack: ReleasePack, stem: str = "<sha256>") -> str:
    """How IDA names an installed signature: its path relative to ``sig/<proc>/``, without ``.sig``."""
    return f"{INSTALL_SUBDIR}/{pack.component}/{pack_folder_name(pack)}/{stem}"


class InstallManifest(BaseModel):
    """``.hcli-pack.json``: which pack build a folder holds."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = MANIFEST_SCHEMA_VERSION
    format: PackFormat = PackFormat.FLIRT
    address: str
    revision: int
    sha256: str
    snapshot: str
    installed_at: str
    sigs: int


@dataclass(frozen=True)
class Installed:
    """What a pack folder holds: a manifest, or why it does not count as installed."""

    manifest: InstallManifest | None
    # Set when the folder exists but has no usable manifest (it is reinstalled).
    problem: str | None = None


def _count_sigs(target: Path) -> int:
    with os.scandir(target) as entries:
        return sum(1 for entry in entries if entry.name.endswith(SIG_SUFFIX) and entry.is_file(follow_symlinks=False))


def read_installed(target: Path, pack: ReleasePack) -> Installed:
    """The pack folder's install state; never raises (an unusable manifest is a ``problem``).

    Beyond the manifest, only the number of ``*.sig`` files is checked (one
    directory listing): a folder missing signatures, e.g. one lost in a
    crash or deleted by hand, is installed again.
    """
    path = target / MANIFEST_NAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Installed(None, "folder exists without a manifest" if target.exists() else None)
    except OSError as e:
        return Installed(None, f"manifest unreadable ({e.strerror or e})")
    try:
        manifest = InstallManifest.model_validate_json(data)
    except ValidationError:
        return Installed(None, "manifest is corrupt")
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        return Installed(None, f"manifest has schema_version {manifest.schema_version}")
    if manifest.address != pack.address or manifest.format is not PackFormat.FLIRT:
        return Installed(None, f"manifest names another pack ({manifest.format.value} {manifest.address})")
    try:
        count = _count_sigs(target)
    except OSError as e:
        return Installed(None, f"folder unreadable ({e.strerror or e})")
    if count != manifest.sigs:
        return Installed(None, f"folder holds {count:,} signatures, its manifest records {manifest.sigs:,}")
    return Installed(manifest)


def _sig_name(member: tarfile.TarInfo, label: str) -> str:
    """The file name a member is extracted as; IntegrityError for anything but a regular ``sigs/<sha256>.sig``."""
    match = _SIG_MEMBER.fullmatch(member.name)
    if match is None or not member.isreg():
        kind = "regular file" if member.isreg() else "non-regular member"
        raise IntegrityError(
            f"{label} contains an unexpected {kind} {member.name!r}: a FLIRT pack holds only sigs/<sha256>.sig files.",
            _BAD_ARCHIVE_HINT,
        )
    return match.group(1)


def _write_synced(path: Path, chunks: Iterable[bytes]) -> None:
    """Create ``path`` (it must not exist), write ``chunks`` and fsync it; LocalIOError("write") on failure.

    Only failures of the write side are reported as such: an exception
    raised while producing ``chunks`` passes through unchanged.
    """
    try:
        out = path.open("xb")
    except OSError as e:
        raise LocalIOError("write", path, e) from e
    try:
        for chunk in chunks:
            try:
                out.write(chunk)
            except OSError as e:
                raise LocalIOError("write", path, e) from e
        try:
            out.flush()
            os.fsync(out.fileno())
        except OSError as e:
            raise LocalIOError("write", path, e) from e
    finally:
        with contextlib.suppress(OSError):
            out.close()


def _member_chunks(data: IO[bytes], archive: Path) -> Iterator[bytes]:
    """A member's bytes; a failure reading the archive is LocalIOError("read") of the archive."""
    while True:
        try:
            chunk = data.read(_CHUNK)
        except OSError as e:
            raise LocalIOError("read", archive, e) from e
        if not chunk:
            return
        yield chunk


def fsync_dir(path: Path) -> None:
    """Flush a folder's entries (new names, renames) to disk, where the OS supports it.

    Windows cannot open a folder for fsync, and some filesystems refuse it:
    both are skipped silently, as there is nothing more to do there.
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)


def extract_sigs(archive: Path, dest: Path, label: str) -> int:
    """Extract the ``sigs/*.sig`` files of a zstd tar into the existing, empty ``dest``; returns how many.

    Streams the archive and writes nothing but regular files directly in
    ``dest``, each fsynced, under names checked to be safe on every OS: any
    other member (another path, a traversal, a link, a device, a duplicate)
    fails the whole extraction with IntegrityError, leaving ``dest`` for the
    caller to remove. A failure reading the archive is LocalIOError("read")
    of the archive; only a failure writing into ``dest`` is
    LocalIOError("write").
    """
    seen: set[str] = set()
    try:
        source = archive.open("rb")
    except OSError as e:
        raise LocalIOError("read", archive, e) from e
    try:
        with (
            source,
            zstandard.ZstdDecompressor().stream_reader(source, read_across_frames=True) as reader,
            tarfile.open(fileobj=reader, mode="r|") as tar,
        ):
            for member in tar:
                name = _sig_name(member, label)
                if name in seen:
                    raise IntegrityError(f"{label} contains {member.name!r} twice.", _BAD_ARCHIVE_HINT)
                seen.add(name)
                data = tar.extractfile(member)
                if data is None:  # unreachable for a regular file; keeps the types honest
                    raise IntegrityError(f"{label}: cannot read {member.name!r}.", _BAD_ARCHIVE_HINT)
                _write_synced(dest / name, _member_chunks(data, archive))
    except (tarfile.TarError, zstandard.ZstdError, EOFError) as e:
        raise IntegrityError(f"{label} is not a valid zstd tar archive: {e}.", _BAD_ARCHIVE_HINT) from e
    except OSError as e:
        # Reading the archive itself (headers, decompression input); writes are classified above.
        raise LocalIOError("read", archive, e) from e
    if not seen:
        raise IntegrityError(f"{label} contains no signatures.", _BAD_ARCHIVE_HINT)
    return len(seen)


def work_dir(root: Path) -> Path:
    """``<sig-dir>/.hcli-work``: where installs are staged and set aside (outside every ``<proc>/``)."""
    return root / WORK_SUBDIR


def _work_path(work: Path, tag: str) -> Path:
    """A fresh name in the work folder: ``<random>.<tag>``."""
    while True:
        candidate = work / f"{secrets.token_hex(8)}.{tag}"
        if not candidate.exists():
            return candidate


def _remove(path: Path) -> None:
    """Best-effort removal of a folder (or a stray file) hcli set aside."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        with contextlib.suppress(OSError):
            path.unlink()


def _sweep_stale_work(work: Path) -> None:
    """Remove the work folders a killed install left behind, once a day old (a running one may own a fresh one)."""
    cutoff = time.time() - _STALE_WORK_AGE
    with contextlib.suppress(OSError):
        for entry in work.iterdir():
            if not _WORK_NAME.match(entry.name):
                continue
            with contextlib.suppress(OSError):
                if entry.lstat().st_mtime < cutoff:
                    _remove(entry)


def swap_into_place(new: Path, target: Path, work: Path) -> None:
    """Make ``new`` (a folder) be ``target``, replacing whatever ``target`` was.

    A rename never lands on an existing path (Windows refuses that for
    folders, POSIX for non-empty ones): the old ``target`` is renamed aside
    into ``work`` first, then ``new`` is renamed in, then the old one is
    removed. When the second rename fails, the old one is put back (and when
    that fails too, the error names where it was left). In between,
    ``target`` does not exist at all, which reads as not installed.
    """
    old: Path | None = None
    if target.exists() or target.is_symlink():
        old = _work_path(work, "old")
        try:
            os.rename(target, old)
        except OSError as e:
            raise LocalIOError("move aside the previous install", target, e) from e
        # A rename keeps the folder's old mtime: refresh it, or a concurrent
        # install's stale sweep would take this in-flight folder for a day-old one.
        with contextlib.suppress(OSError):
            os.utime(old)
    try:
        os.rename(new, target)
    except OSError as e:
        error = LocalIOError("move the new install into place at", target, e)
        if old is not None:
            try:
                os.rename(old, target)
            except OSError:
                error.hint = (
                    f"the previous install was left at {old}; move it back to {target} to keep using it, "
                    f"then {error.hint}"
                )
        raise error from e
    fsync_dir(target.parent)
    if old is not None:
        _remove(old)


def install_pack(archive: Path, pack: ReleasePack, snapshot: str, root: Path) -> InstallManifest:
    """Install the verified ``archive`` of ``pack`` into ``install_dir(root, pack)``, atomically (see the module docs)."""
    target = install_dir(root, pack)
    work = work_dir(root)
    for folder in (target.parent, work):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LocalIOError("create the folder", folder, e, directory=folder) from e
    _sweep_stale_work(work)
    new = _work_path(work, "new")
    try:
        new.mkdir()
    except OSError as e:
        raise LocalIOError("create a temporary folder for", target, e, directory=work) from e
    try:
        count = extract_sigs(archive, new, f"FLIRT pack {pack.address} r{pack.revision}")
        manifest = InstallManifest(
            address=pack.address,
            revision=pack.revision,
            sha256=pack.sha256,
            snapshot=snapshot,
            installed_at=utc_now(),
            sigs=count,
        )
        # Last: a folder holding a manifest is a complete install.
        document = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        _write_synced(new / MANIFEST_NAME, [document.encode()])
        fsync_dir(new)
        swap_into_place(new, target, work)
    finally:
        if new.exists():
            _remove(new)
    return manifest


class InstallAction(str, Enum):
    """What ``sync``/``install`` plans for a pack; the values are the ``--json`` slugs."""

    INSTALL = "install"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"  # the installed build has a higher revision than stable
    UP_TO_DATE = "up_to_date"
    UNSUPPORTED = "unsupported"  # an arch hcli cannot install: skipped


# How the plan table names each action.
ACTION_TEXT: Final = {
    InstallAction.INSTALL: "install",
    InstallAction.UPGRADE: "install (upgrade)",
    InstallAction.DOWNGRADE: "install (downgrade)",
    InstallAction.UP_TO_DATE: "up to date",
    InstallAction.UNSUPPORTED: "unsupported",
}


@dataclass(frozen=True)
class InstallItem:
    pack: ReleasePack
    # None only for an UNSUPPORTED pack, which has no folder.
    target: Path | None
    installed: Installed
    action: InstallAction


def plan_install(packs: Sequence[ReleasePack], root: Path) -> list[InstallItem]:
    """Per pack: not installed (or no usable manifest) -> install; same sha256 -> up to date; other bytes ->
    upgrade, or downgrade when the installed revision is higher than stable.

    A pack of an arch hcli cannot install is planned as UNSUPPORTED (never
    raises): callers skip it, or reject it when the user named it exactly.
    """
    items: list[InstallItem] = []
    for pack in packs:
        if not is_supported(pack):
            items.append(InstallItem(pack, None, Installed(None), InstallAction.UNSUPPORTED))
            continue
        target = install_dir(root, pack)
        installed = read_installed(target, pack)
        if installed.manifest is None:
            action = InstallAction.INSTALL
        elif installed.manifest.sha256 == pack.sha256:
            action = InstallAction.UP_TO_DATE
        elif installed.manifest.revision > pack.revision:
            action = InstallAction.DOWNGRADE
        else:
            action = InstallAction.UPGRADE
        items.append(InstallItem(pack, target, installed, action))
    return items
