from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import shutil
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import IO, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hcli.lib.ida.plugin.repo import (
    BasePluginRepo,
    Plugin,
    PluginArchiveIndex,
    PluginArchiveLocation,
)

logger = logging.getLogger(__name__)

BUNDLE_KIND = "hcli-plugin-bundle"
BUNDLE_VERSION = 1


class PluginBundleCreatedBy(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    version: str


class PluginBundleTargetPlatformTag(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    ida_platform: str = Field(alias="idaPlatform")
    python_version: str = Field(alias="pythonVersion")
    implementation: str
    abis: list[str]
    pip_platform_tags: list[str] = Field(alias="pipPlatformTags")
    wheelhouse: str


class PluginBundleManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal[1]
    kind: Literal["hcli-plugin-bundle"]
    built_at: datetime = Field(alias="builtAt")
    created_by: PluginBundleCreatedBy = Field(alias="createdBy")
    target_platform_tags: list[PluginBundleTargetPlatformTag] = Field(alias="targetPlatformTags")

    @field_validator("target_platform_tags")
    @classmethod
    def no_duplicate_target_ids(cls, v: list[PluginBundleTargetPlatformTag]) -> list[PluginBundleTargetPlatformTag]:
        ids = [t.id for t in v]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate target IDs: {ids}")
        return v


def _validate_bundle_path(member_path: str) -> None:
    p = pathlib.PurePosixPath(member_path)
    if p.is_absolute():
        raise ValueError(f"absolute path in bundle: {member_path}")
    if ".." in p.parts:
        raise ValueError(f"path traversal in bundle: {member_path}")
    if "\\" in member_path:
        raise ValueError(f"backslash in bundle path: {member_path}")
    if ":" in member_path:
        raise ValueError(f"drive letter or stream in bundle path: {member_path}")


def is_plugin_bundle_zip(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix != ".zip":
        return False
    try:
        with zipfile.ZipFile(path, "r") as zf:
            return "plugin-bundle.json" in zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def is_plugin_bundle_dir(path: Path) -> bool:
    """Whether `path` is an extracted plugin bundle: a directory with a top-level `plugin-bundle.json`."""
    return path.is_dir() and (path / "plugin-bundle.json").is_file()


def is_plugin_bundle(path: Path) -> bool:
    return is_plugin_bundle_zip(path) or is_plugin_bundle_dir(path)


class _BundleFiles(Protocol):
    """Read access to the files of a bundle, by bundle-relative POSIX path."""

    def names(self) -> Iterator[str]: ...

    def read(self, name: str) -> bytes: ...

    def open(self, name: str) -> IO[bytes]: ...

    def is_symlink(self, name: str) -> bool: ...

    def close(self) -> None: ...


class _ZipBundleFiles:
    def __init__(self, path: Path):
        self._zf = zipfile.ZipFile(path, "r")

    def names(self) -> Iterator[str]:
        return (info.filename for info in self._zf.infolist() if not info.is_dir())

    def read(self, name: str) -> bytes:
        return self._zf.read(name)

    def open(self, name: str) -> IO[bytes]:
        return self._zf.open(name)

    def is_symlink(self, name: str) -> bool:
        return (self._zf.getinfo(name).external_attr >> 28) == 0xA

    def close(self) -> None:
        self._zf.close()


class _DirBundleFiles:
    def __init__(self, path: Path):
        self._root = path

    def names(self) -> Iterator[str]:
        for dirpath, _, filenames in os.walk(self._root):
            for filename in filenames:
                yield (Path(dirpath) / filename).relative_to(self._root).as_posix()

    def _path(self, name: str) -> Path:
        _validate_bundle_path(name)
        return self._root / name

    def read(self, name: str) -> bytes:
        return self._path(name).read_bytes()

    def open(self, name: str) -> IO[bytes]:
        return self._path(name).open("rb")

    def is_symlink(self, name: str) -> bool:
        return self._path(name).is_symlink()

    def close(self) -> None:
        pass


def _load_manifest(files: _BundleFiles) -> PluginBundleManifest:
    raw = files.read("plugin-bundle.json").decode("utf-8")
    manifest = PluginBundleManifest.model_validate_json(raw)

    names = list(files.names())
    for target in manifest.target_platform_tags:
        _validate_bundle_path(target.wheelhouse)
        wh_prefix = target.wheelhouse.rstrip("/") + "/"
        if not any(name.startswith(wh_prefix) for name in names):
            logger.debug("wheelhouse path not found in bundle: %s", target.wheelhouse)

    return manifest


class PluginBundleRepo(BasePluginRepo):
    """Plugins and wheelhouses of a plugin bundle, from the bundle zip or from the directory that extracting it gives."""

    def __init__(self, path: Path):
        self._files: _BundleFiles = _DirBundleFiles(path) if path.is_dir() else _ZipBundleFiles(path)
        try:
            self._manifest = _load_manifest(self._files)
        except Exception:
            self._files.close()
            raise

    @property
    def manifest(self) -> PluginBundleManifest:
        return self._manifest

    @property
    def built_at(self) -> datetime:
        return self._manifest.built_at

    @property
    def target_ids(self) -> list[str]:
        return [t.id for t in self._manifest.target_platform_tags]

    def close(self):
        self._files.close()

    def get_plugins(self) -> list[Plugin]:
        return self._load_plugins_by_walking()

    def _load_plugins_by_walking(self) -> list[Plugin]:
        index = PluginArchiveIndex()
        for name in sorted(self._files.names()):
            if not name.startswith("plugins/"):
                continue
            if not name.endswith(".zip"):
                continue
            _validate_bundle_path(name)

            try:
                plugin_zip_data = self._files.read(name)
            except (KeyError, OSError, zipfile.BadZipFile) as e:
                logger.debug("skipping unreadable plugin archive %s: %s", name, e)
                continue

            bundle_url = f"hcli-bundle:{name}"
            index.index_plugin_archive(plugin_zip_data, bundle_url, context={"bundle_member": name})

        return index.get_plugins()

    def _fetch_and_verify(self, location: PluginArchiveLocation) -> tuple[str, bytes]:
        plugin_name = location.metadata.plugin.name
        url = location.url

        if url.startswith("hcli-bundle:"):
            member_path = url[len("hcli-bundle:") :]
            buf = self._files.read(member_path)
        else:
            from hcli.lib.ida.plugin.repo import fetch_plugin_archive

            buf = fetch_plugin_archive(url)

        h = hashlib.sha256()
        h.update(buf)
        sha256 = h.hexdigest()

        if sha256 != location.sha256:
            raise ValueError(f"hash mismatch: expected {location.sha256} but found {sha256} for {url}")

        return plugin_name, buf

    def find_target_for_platform(self, ida_platform: str, python_version: str) -> PluginBundleTargetPlatformTag | None:
        for target in self._manifest.target_platform_tags:
            if target.ida_platform == ida_platform and target.python_version == python_version:
                return target
        return None

    def extract_wheelhouse(self, target: PluginBundleTargetPlatformTag, dest: Path) -> None:
        """Copy the wheels of `target` into `dest`, flattened.

        Raises:
            ValueError: when a wheelhouse path escapes the bundle, is a symlink, or two wheels have the same filename.
        """
        dest.mkdir(parents=True, exist_ok=True)
        wh_prefix = target.wheelhouse.rstrip("/") + "/"
        seen: set[str] = set()

        for name in sorted(self._files.names()):
            if not name.startswith(wh_prefix):
                continue

            _validate_bundle_path(name)

            relative = pathlib.PurePosixPath(name).relative_to(wh_prefix.rstrip("/"))
            if ".." in relative.parts:
                raise ValueError(f"path traversal in wheelhouse: {name}")

            if self._files.is_symlink(name):
                raise ValueError(f"symlink in wheelhouse: {name}")

            if relative.name in seen:
                raise ValueError(f"duplicate filename in wheelhouse: {relative.name} (from {name})")
            seen.add(relative.name)

            target_file = dest / relative.name
            if target_file.resolve().parent != dest.resolve():
                raise ValueError(f"path traversal in wheelhouse: {name}")
            with self._files.open(name) as src, target_file.open("wb") as dst:
                shutil.copyfileobj(src, dst)
