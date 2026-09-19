from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.tags import mac_platforms

from hcli.lib.ida.plugin.repo import BasePluginRepo
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo, PluginBundleTargetPlatformTag
from hcli.lib.ida.python import PipOptions

if TYPE_CHECKING:
    from hcli.lib.ida.plugin.resolve import ArchiveRoot, RepositoryRoot

logger = logging.getLogger(__name__)

MINIMUM_PYTHON_VERSION = (3, 10)

SUPPORTED_PYTHON_VERSIONS: tuple[str, ...] = ("3.10", "3.11", "3.12", "3.13", "3.14")

PLATFORM_ALIASES: dict[str, list[str]] = {
    "windows-x86_64": ["windows", "win", "win64"],
    "windows-aarch64": ["windows-arm64", "win-arm64"],
    "linux-x86_64": ["linux", "linux64"],
    "linux-aarch64": ["linux-arm64"],
    "macos-aarch64": ["macos-arm64", "macos-arm", "mac-arm64"],
    "macos-x86_64": ["macos-intel", "mac-intel", "macos-x64"],
}

ALL_PLATFORMS: tuple[str, ...] = tuple(PLATFORM_ALIASES.keys())


def resolve_platform_alias(name: str) -> str:
    """Resolve a platform name or alias to the canonical ida_platform string.

    Raises:
        ValueError: with a helpful message listing valid names.
    """
    lower = name.lower().strip()
    if lower in _PLATFORM_CONFIG:
        return lower
    for canonical, aliases in PLATFORM_ALIASES.items():
        if lower in aliases:
            return canonical
    valid = []
    for canonical, aliases in PLATFORM_ALIASES.items():
        valid.append(f"  {canonical} (or: {', '.join(aliases)})")
    raise ValueError(f"unknown platform: {name!r}\nvalid platforms:\n" + "\n".join(valid))


_PLATFORM_CONFIG: dict[str, dict] = {
    "windows-x86_64": {
        "pip_platform_tags": ("win_amd64",),
    },
    "windows-aarch64": {
        "pip_platform_tags": ("win_arm64",),
    },
    "linux-x86_64": {
        "max_glibc": (2, 28),
        "arch": "x86_64",
    },
    "linux-aarch64": {
        "max_glibc": (2, 28),
        "arch": "aarch64",
    },
    "macos-aarch64": {
        "mac_version": (11, 0),
        "arch": "arm64",
    },
    "macos-x86_64": {
        "mac_version": (10, 13),
        "arch": "x86_64",
    },
}


def _manylinux_tags(max_glibc: tuple[int, int], arch: str) -> tuple[str, ...]:
    major, minor = max_glibc
    tags: list[str] = []
    for m in range(minor, 4, -1):
        tags.append(f"manylinux_{major}_{m}_{arch}")
    if max_glibc >= (2, 17):
        tags.append(f"manylinux2014_{arch}")
    if max_glibc >= (2, 12):
        tags.append(f"manylinux2010_{arch}")
    if max_glibc >= (2, 5):
        tags.append(f"manylinux1_{arch}")
    return tuple(tags)


def _mac_platform_tags(version: tuple[int, int], arch: str) -> tuple[str, ...]:
    return tuple(mac_platforms(version=version, arch=arch))


def _build_pip_platform_tags(ida_platform: str) -> tuple[str, ...]:
    config = _PLATFORM_CONFIG.get(ida_platform)
    if config is None:
        available = ", ".join(sorted(_PLATFORM_CONFIG))
        raise ValueError(f"unsupported platform: {ida_platform} (available: {available})")

    if "pip_platform_tags" in config:
        return config["pip_platform_tags"]
    elif "max_glibc" in config:
        return _manylinux_tags(config["max_glibc"], config["arch"])
    elif "mac_version" in config:
        return _mac_platform_tags(config["mac_version"], config["arch"])
    else:
        raise ValueError(f"incomplete platform config for {ida_platform}")


def _parse_python_version(version: str) -> tuple[int, int]:
    m = re.match(r"^(\d+)\.(\d+)$", version)
    if not m:
        raise ValueError(f"invalid python version: {version!r} (expected 'major.minor')")
    return int(m.group(1)), int(m.group(2))


@dataclass(frozen=True)
class PipTarget:
    ida_platform: str
    python_version: str

    @classmethod
    def parse(cls, target_id: str) -> PipTarget:
        """Parse a target ID like 'linux-x86_64-cp312' into a PipTarget.

        Raises:
            ValueError: if the string can't be parsed or represents an unsupported target.
        """
        m = re.match(r"^(.+)-cp(\d)(\d+)$", target_id)
        if not m:
            raise ValueError(
                f"invalid target ID: {target_id!r}\n"
                f"expected format: <platform>-cp<ver> (e.g. 'linux-x86_64-cp312')\n"
                f"hint: prefer --platform and --python instead of --target"
            )
        ida_platform = m.group(1)
        python_version = f"{m.group(2)}.{m.group(3)}"
        resolve_platform_alias(ida_platform)
        ver = _parse_python_version(python_version)
        if ver < MINIMUM_PYTHON_VERSION:
            raise ValueError(
                f"python {python_version} is below minimum {MINIMUM_PYTHON_VERSION[0]}.{MINIMUM_PYTHON_VERSION[1]}"
            )
        return cls(ida_platform=ida_platform, python_version=python_version)

    @property
    def id(self) -> str:
        return f"{self.ida_platform}-cp{self.python_version.replace('.', '')}"

    @property
    def abis(self) -> tuple[str, ...]:
        ver = self.python_version.replace(".", "")
        return (f"cp{ver}", "abi3", "none")

    @property
    def pip_platform_tags(self) -> tuple[str, ...]:
        return _build_pip_platform_tags(self.ida_platform)

    def pip_download_args(self) -> list[str]:
        args = [
            "--only-binary=:all:",
            "--implementation",
            "cp",
            "--python-version",
            self.python_version,
        ]
        for abi in self.abis:
            args.extend(["--abi", abi])
        for tag in self.pip_platform_tags:
            args.extend(["--platform", tag])
        return args


def to_manifest_target(target: PipTarget, wheelhouse_path: str) -> PluginBundleTargetPlatformTag:
    return PluginBundleTargetPlatformTag.model_validate(
        {
            "id": target.id,
            "idaPlatform": target.ida_platform,
            "pythonVersion": target.python_version,
            "implementation": "cp",
            "abis": list(target.abis),
            "pipPlatformTags": list(target.pip_platform_tags),
            "wheelhouse": wheelhouse_path,
        }
    )


@contextmanager
def bundle_dependency_source(
    repo: PluginBundleRepo,
    ida_platform: str,
    python_version: str,
) -> Iterator[PipOptions | None]:
    target = repo.find_target_for_platform(ida_platform, python_version)
    if target is None:
        yield None
        return

    with tempfile.TemporaryDirectory(prefix="hcli-bundle-wh-") as tmpdir:
        wh_path = Path(tmpdir)
        repo.extract_wheelhouse(target, wh_path)
        yield PipOptions(
            isolated=True,
            no_cache_dir=True,
            disable_pip_version_check=True,
            find_links=(wh_path,),
        )


@dataclass(frozen=True)
class BundleArchive:
    """One plugin archive selected for a bundle and the target platforms it serves."""

    name: str
    version: str
    sha256: str
    data: bytes
    platforms: tuple[str, ...]
    is_root: bool

    @property
    def filename(self) -> str:
        return f"{self.name}-{self.version}.zip"


@dataclass(frozen=True)
class OmittedOptionalDependency:
    """An optional dependency the bundle leaves out, and why."""

    spec: str
    declared_by: str
    reason: str


@dataclass
class BundleContents:
    """Archives and Python requirements that make up a bundle."""

    archives: list[BundleArchive] = field(default_factory=list)
    python_requirements: list[str] = field(default_factory=list)
    omitted_optional: list[OmittedOptionalDependency] = field(default_factory=list)

    def archives_for_name(self, name: str) -> list[BundleArchive]:
        return [a for a in self.archives if a.name == name]


def _bundle_root(spec: str, repo: BasePluginRepo | None) -> ArchiveRoot | RepositoryRoot:
    """Turn one ``bundle create`` argument into a planner root.

    Raises:
        ValueError: when a repository reference is malformed or no repository is available.
    """
    from hcli.lib.ida.plugin.install import pack_plugin_directory_to_zip
    from hcli.lib.ida.plugin.reference import parse_plugin_reference
    from hcli.lib.ida.plugin.resolve import ArchiveRoot, RepositoryRoot

    path = Path(spec).expanduser()
    if path.is_dir() and (path / "ida-plugin.json").is_file():
        return ArchiveRoot(pack_plugin_directory_to_zip(path.resolve()))
    if path.is_file() and spec.endswith(".zip"):
        return ArchiveRoot(path.read_bytes())
    if repo is None:
        raise ValueError(f"no plugin repository available to resolve '{spec}'")
    return RepositoryRoot(parse_plugin_reference(spec), repo)


def plan_bundle_contents(
    specs: Sequence[str],
    repo: BasePluginRepo | None,
    target_platforms: Sequence[str],
) -> BundleContents:
    """Select every archive a bundle needs for ``specs`` on ``target_platforms``.

    Each platform is planned separately against an empty installed state so the
    builder's own plugins never satisfy a dependency. The required closure of
    every root is included; optional dependencies are included only when named
    as roots, and each omitted one is listed in ``omitted_optional``. Archives
    are deduplicated by digest across roots and platforms.

    Raises:
        DependencyResolutionError: a required dependency is unavailable or conflicts.
        PlanMetadataMismatchError: a fetched archive's metadata differs from the index entry.
        PlatformIncompatibleError: a local root does not support a target platform.
        ValueError: a spec is malformed, an archive digest does not match, or an
            archive cannot be read.
    """
    import hashlib

    from hcli.lib.ida import IDAConfigJson
    from hcli.lib.ida.plugin.execute import verify_planned_archive
    from hcli.lib.ida.plugin.resolve import (
        LocalArchiveSource,
        LocationSource,
        ResolutionContext,
        plan_install,
    )

    roots = [_bundle_root(spec, repo) for spec in specs]
    by_sha: dict[str, tuple[str, str, bytes, bool]] = {}
    platforms_by_sha: dict[str, list[str]] = {}
    fetched: dict[str, bytes] = {}
    verified: set[str] = set()
    requirements: dict[str, None] = {}
    omitted: dict[tuple[str, str], OmittedOptionalDependency] = {}

    for platform in target_platforms:
        context = ResolutionContext(
            current_platform=platform,
            current_version=None,
            installed=[],
            installed_config=IDAConfigJson(),
            dependency_repo=repo,
        )
        plan = plan_install(context, roots)
        for requirement in plan.combined_python_requirements():
            requirements.setdefault(requirement, None)
        for branch in plan.optional_branches:
            target = branch.target
            if target is not None and target in plan.nodes:
                continue
            spec = branch.edge.spec.plugin
            declared_by = branch.edge.chain[-1] if branch.edge.chain else "?"
            reason = branch.unavailable_reason or "optional dependencies are bundled only when named as roots"
            omitted.setdefault((spec, declared_by), OmittedOptionalDependency(spec, declared_by, reason))

        for node in plan.ordered_nodes():
            source = node.source
            if isinstance(source, LocalArchiveSource):
                sha256 = source.sha256
                data = context.artifacts.get(sha256)
            elif isinstance(source, LocationSource):
                sha256 = source.location.sha256
                if sha256 not in fetched:
                    if sha256 in context.artifacts:
                        fetched[sha256] = context.artifacts.get(sha256)
                    else:
                        fetched[sha256] = source.repo.fetch_location(source.location)
                data = fetched[sha256]
                if sha256 not in verified:
                    digest = hashlib.sha256(data).hexdigest()
                    if digest != sha256:
                        raise ValueError(f"hash mismatch for {source.url}: expected {sha256}, found {digest}")
                    verify_planned_archive(node, data, source.repo_name or source.url)
                    verified.add(sha256)
            else:
                raise TypeError(f"cannot bundle {node.name} from {type(source).__name__}")

            existing = by_sha.get(sha256)
            by_sha[sha256] = (node.name, node.version, data, node.is_root or (existing is not None and existing[3]))
            platforms_by_sha.setdefault(sha256, []).append(platform)

    contents = BundleContents(python_requirements=list(requirements), omitted_optional=list(omitted.values()))
    for sha256, (name, version, data, is_root) in by_sha.items():
        platforms = tuple(dict.fromkeys(platforms_by_sha[sha256]))
        contents.archives.append(BundleArchive(name, version, sha256, data, platforms, is_root))
    return contents
