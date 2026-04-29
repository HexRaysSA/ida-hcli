from __future__ import annotations

import hashlib
import logging
import pathlib
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal

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


def load_manifest_from_zip(zf: zipfile.ZipFile) -> PluginBundleManifest:
    raw = zf.read("plugin-bundle.json").decode("utf-8")
    manifest = PluginBundleManifest.model_validate_json(raw)

    for target in manifest.target_platform_tags:
        _validate_bundle_path(target.wheelhouse)
        wh_prefix = target.wheelhouse.rstrip("/") + "/"
        if not any(name.startswith(wh_prefix) or name == target.wheelhouse + "/" for name in zf.namelist()):
            logger.debug("wheelhouse path not found in archive: %s", target.wheelhouse)

    return manifest


class PluginBundleRepo(BasePluginRepo):
    def __init__(self, path: Path):
        self._path = path
        self._zf = zipfile.ZipFile(path, "r")
        try:
            self._manifest = load_manifest_from_zip(self._zf)
        except Exception:
            self._zf.close()
            raise

    @property
    def path(self) -> Path:
        return self._path

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
        self._zf.close()

    def get_plugins(self) -> list[Plugin]:
        return self._load_plugins_by_walking()

    def _load_plugins_by_walking(self) -> list[Plugin]:
        index = PluginArchiveIndex()
        for name in self._zf.namelist():
            if not name.startswith("plugins/"):
                continue
            if not name.endswith(".zip"):
                continue
            _validate_bundle_path(name)

            try:
                plugin_zip_data = self._zf.read(name)
            except (KeyError, zipfile.BadZipFile) as e:
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
            buf = self._zf.read(member_path)
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
        dest.mkdir(parents=True, exist_ok=True)
        wh_prefix = target.wheelhouse.rstrip("/") + "/"

        for info in self._zf.infolist():
            if not info.filename.startswith(wh_prefix):
                continue
            if info.is_dir():
                continue

            _validate_bundle_path(info.filename)

            relative = pathlib.PurePosixPath(info.filename).relative_to(wh_prefix.rstrip("/"))
            if ".." in relative.parts:
                raise ValueError(f"path traversal in wheelhouse: {info.filename}")

            if (info.external_attr >> 28) == 0xA:
                raise ValueError(f"symlink in wheelhouse: {info.filename}")

            target_file = dest / relative.name
            with self._zf.open(info.filename) as src, target_file.open("wb") as dst:
                import shutil

                shutil.copyfileobj(src, dst)
