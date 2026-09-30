from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import rich.status
import rich_click as click

from hcli import __version__ as hcli_version
from hcli.lib.console import console, stderr_console
from hcli.lib.ida.plugin import (
    get_metadatas_with_paths_from_plugin_archive,
    get_python_dependencies_from_plugin_archive,
    get_version_from_plugin_archive,
    is_python_version_compatible,
)
from hcli.lib.ida.plugin.bundle import (
    ALL_PLATFORMS,
    SUPPORTED_PYTHON_VERSIONS,
    PipTarget,
    resolve_platform_alias,
    to_manifest_target,
)
from hcli.lib.ida.plugin.components import find_root_manifest_in_archive
from hcli.lib.ida.plugin.reference import DependencyEntry, parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveIndex, PluginArchiveLocation
from hcli.lib.ida.plugin.repo.bundle import (
    PluginBundleRepo,
    is_plugin_bundle_zip,
)
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions, find_current_python_executable

logger = logging.getLogger(__name__)


@click.group()
def bundle() -> None:
    """Manage plugin bundles for offline installation."""


@bundle.command()
@click.argument("bundle_path", type=click.Path(exists=True))
def info(bundle_path: str) -> None:
    """Show plugin bundle metadata."""
    path = Path(bundle_path)
    if not is_plugin_bundle_zip(path):
        console.print(f"[red]Error[/red]: {path} is not a plugin bundle")
        raise click.Abort()

    repo = PluginBundleRepo(path)
    try:
        console.print(f"[bold]plugin bundle[/bold]: {path}")
        console.print(f"  built: {repo.built_at.isoformat()}")
        console.print(f"  created by: {repo.manifest.created_by.tool} {repo.manifest.created_by.version}")
        console.print(f"  targets: {', '.join(repo.target_ids)}")

        plugins = repo.get_plugins()
        if plugins:
            console.print(f"  plugins: {len(plugins)}")
            for plugin in plugins:
                versions = sorted(plugin.versions.keys())
                console.print(f"    {plugin.name}: {', '.join(versions)}")
        else:
            console.print("  plugins: (none)")
    finally:
        repo.close()


def _is_local_plugin_spec(spec: str) -> bool:
    path = Path(spec).expanduser()
    return (path.is_dir() and (path / "ida-plugin.json").is_file()) or (path.exists() and spec.endswith(".zip"))


def _resolve_plugin_bytes(
    spec: str,
    plugin_repo: BasePluginRepo | None,
    current_platform: str | None = None,
    python_version: str | None = None,
) -> tuple[str, bytes]:
    path = Path(spec).expanduser()

    if path.is_dir() and (path / "ida-plugin.json").is_file():
        from hcli.lib.ida.plugin.install import pack_plugin_directory_to_zip

        buf = pack_plugin_directory_to_zip(path.resolve())
        _, meta = find_root_manifest_in_archive(buf)
        return meta.plugin.name, buf

    if path.exists() and spec.endswith(".zip"):
        buf = path.read_bytes()
        _, meta = find_root_manifest_in_archive(buf)
        return meta.plugin.name, buf

    host: str | None = None
    clean_spec = spec
    try:
        ref = parse_plugin_reference(spec)
        host = ref.host
        clean_spec = f"{ref.name}{ref.version_spec}" if ref.version_spec else ref.name
    except ValueError:
        pass

    if plugin_repo is None:
        raise click.BadParameter("no plugin repository available to resolve spec")

    return plugin_repo.fetch_plugin_from_spec(clean_spec, current_platform, host=host, python_version=python_version)


def _resolve_targets(
    platforms: tuple[str, ...],
    pythons: tuple[str, ...],
    targets: tuple[str, ...],
) -> list[PipTarget]:
    """Resolve CLI options into a list of PipTarget instances.

    Raises:
        click.BadParameter: on invalid input.
    """
    if targets and (platforms or pythons):
        raise click.BadParameter("--target cannot be combined with --platform or --python")

    if targets:
        parsed: list[PipTarget] = []
        for t in targets:
            try:
                parsed.append(PipTarget.parse(t))
            except ValueError as e:
                raise click.BadParameter(str(e))
        return parsed

    if not platforms:
        raise click.BadParameter(
            "--platform is required\n"
            "  use --platform current for this machine, or --platform all for all supported platforms"
        )
    if not pythons:
        raise click.BadParameter(
            "--python is required\n  use --python current for this machine, or --python all for all supported versions"
        )

    resolved_platforms: list[str] = []
    for p in platforms:
        lower = p.lower().strip()
        if lower == "all":
            resolved_platforms.extend(ALL_PLATFORMS)
        elif lower == "current":
            from hcli.lib.ida import find_current_ida_platform

            resolved_platforms.append(find_current_ida_platform())
        else:
            try:
                resolved_platforms.append(resolve_platform_alias(p))
            except ValueError as e:
                raise click.BadParameter(str(e))

    resolved_pythons: list[str] = []
    for py in pythons:
        lower = py.lower().strip()
        if lower == "all":
            resolved_pythons.extend(SUPPORTED_PYTHON_VERSIONS)
        elif lower == "current":
            from hcli.lib.ida.python import detect_current_python_version

            resolved_pythons.append(detect_current_python_version().major_minor)
        else:
            resolved_pythons.append(py)

    from hcli.lib.ida.plugin.bundle import MINIMUM_PYTHON_VERSION, _parse_python_version

    seen: set[str] = set()
    result: list[PipTarget] = []
    for plat in resolved_platforms:
        for pyv in resolved_pythons:
            try:
                resolve_platform_alias(plat)
                ver = _parse_python_version(pyv)
                if ver < MINIMUM_PYTHON_VERSION:
                    raise ValueError(
                        f"python {pyv} is below minimum {MINIMUM_PYTHON_VERSION[0]}.{MINIMUM_PYTHON_VERSION[1]}"
                    )
                target = PipTarget(ida_platform=plat, python_version=pyv)
            except ValueError as e:
                raise click.BadParameter(str(e))
            if target.id not in seen:
                seen.add(target.id)
                result.append(target)
    return result


@bundle.command("create")
@click.pass_context
@click.option("--path", "output_path", required=True, type=click.Path(), help="output archive path")
@click.option(
    "--platform",
    "platforms",
    multiple=True,
    help="target platform: 'current', 'all', or a name like 'linux', 'windows', 'macos-arm64' (repeatable)",
)
@click.option(
    "--python",
    "pythons",
    multiple=True,
    help="target Python version: 'current', 'all', or a version like '3.12', '3.13' (repeatable)",
)
@click.option("--target", "targets", multiple=True, hidden=True, help="exact target ID (e.g. linux-x86_64-cp312)")
@click.option("--repo", "bundle_repo", default=None, help="plugin repository for resolving specs")
@click.argument("plugin_specs", nargs=-1, required=True)
def create(
    ctx,
    output_path: str,
    platforms: tuple[str, ...],
    pythons: tuple[str, ...],
    targets: tuple[str, ...],
    bundle_repo: str | None,
    plugin_specs: tuple[str, ...],
) -> None:
    """Create a plugin bundle from plugin specs, local directories, and/or ZIPs."""
    pip_options: PipOptions = ctx.obj.get("pip_options", PIP_OPTIONS_DEFAULT)
    parent_repo = ctx.obj.get("plugin_repo")

    if bundle_repo is not None:
        from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
        from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo

        repo_path = Path(bundle_repo)
        if repo_path.is_dir():
            parent_repo = FileSystemPluginRepo(repo_path)
        elif repo_path.exists():
            parent_repo = JSONFilePluginRepo.from_file(repo_path)

    try:
        pip_targets = _resolve_targets(platforms, pythons, targets)
    except click.BadParameter as e:
        console.print(f"[red]error[/red]: {e.format_message()}")
        raise click.Abort()

    stderr_console.print(f"targets ({len(pip_targets)}):")
    for t in pip_targets:
        stderr_console.print(f"  {t.ida_platform}  Python {t.python_version}  ({t.id})")

    with tempfile.TemporaryDirectory(prefix="hcli-bundle-staging-") as staging_dir:
        staging = Path(staging_dir)
        plugins_dir = staging / "plugins"
        plugins_dir.mkdir()
        deps_dir = staging / "dependencies" / "python"
        deps_dir.mkdir(parents=True)

        resolution = _get_cell_closures(plugin_specs, pip_targets, parent_repo)

        plugin_index = PluginArchiveIndex()
        bufs_by_name: dict[str, dict[PipTarget, bytes]] = {}
        for target, closure in resolution.closures.items():
            for name, buf in closure.items():
                bufs_by_name.setdefault(name, {})[target] = buf

        for name, bufs in bufs_by_name.items():
            _stage_plugin_archives(name, bufs, plugins_dir, plugin_index)

        for spec, name in resolution.root_names.items():
            if "==" not in spec and not _is_local_plugin_spec(spec):
                versions = _render_versions_by_target(name, bufs_by_name[name], pip_targets)
                stderr_console.print(f"resolved {spec}: {versions}")

        dependency_names = [name for name in bufs_by_name if name not in resolution.root_names.values()]
        for name in dependency_names:
            versions = _render_versions_by_target(name, bufs_by_name[name], pip_targets)
            stderr_console.print(f"  included dependency: {name} {versions}")

        target_manifests = []
        for target in pip_targets:
            wh_dir = deps_dir / target.id
            wh_dir.mkdir(parents=True, exist_ok=True)

            python_deps = _get_python_deps(resolution.closures[target])
            if python_deps:
                with rich.status.Status(
                    f"downloading wheels for {target.ida_platform} Python {target.python_version}",
                    console=stderr_console,
                ):
                    _download_wheelhouse(python_deps, target, wh_dir, pip_options)
                _verify_wheelhouse(wh_dir, target)

            target_manifests.append(to_manifest_target(target, f"dependencies/python/{target.id}"))

        manifest = {
            "version": 1,
            "kind": "hcli-plugin-bundle",
            "builtAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "createdBy": {"tool": "hcli", "version": hcli_version},
            "targetPlatformTags": [t.model_dump(by_alias=True) for t in target_manifests],
        }

        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        out = Path(output_path)
        with rich.status.Status("writing bundle archive", console=stderr_console):
            _write_bundle_zip(out, manifest_bytes, staging)

    console.print(f"[green]created[/green] plugin bundle: {out}")
    console.print(f"  plugins: {len(bufs_by_name)}")
    if dependency_names:
        console.print(f"    ({len(dependency_names)} resolved as dependencies)")
    console.print(f"  targets: {len(pip_targets)}")
    for t in pip_targets:
        console.print(f"    {t.ida_platform}  Python {t.python_version}")


MAX_LOOSE_DEP_DEPTH = 10


@dataclass
class _BundleResolution:
    """The plugins selected for each target cell.

    Attributes:
        root_names: plugin name for each positional spec.
        closures: plugin archives by plugin name, for each target cell.
    """

    root_names: dict[str, str]
    closures: dict[PipTarget, dict[str, bytes]]


class _CachingPluginRepo(BasePluginRepo):
    """Wraps a plugin repository so that the index and each archive are fetched once across target cells."""

    def __init__(self, inner: BasePluginRepo) -> None:
        self._inner = inner
        self._plugins: list[Plugin] | None = None
        self._archives: dict[tuple[str, str], tuple[str, bytes]] = {}

    def get_plugins(self) -> list[Plugin]:
        if self._plugins is None:
            self._plugins = self._inner.get_plugins()
        return self._plugins

    def _fetch_and_verify(self, location: PluginArchiveLocation) -> tuple[str, bytes]:
        key = (location.url, location.sha256)
        if key not in self._archives:
            self._archives[key] = self._inner._fetch_and_verify(location)
        return self._archives[key]


def _get_cell_closures(
    plugin_specs: tuple[str, ...],
    pip_targets: list[PipTarget],
    plugin_repo: BasePluginRepo | None,
) -> _BundleResolution:
    """Resolve the plugin specs and their loose dependencies separately for each target cell.

    Raises:
        click.BadParameter: when a repository spec is given without a plugin repository.
        RuntimeError: when a plugin spec or a required dependency cannot be resolved for a cell,
            or when a local plugin's requiresPython excludes a cell.
    """
    repo = _CachingPluginRepo(plugin_repo) if plugin_repo is not None else None

    local_roots: dict[str, tuple[str, bytes]] = {}
    for spec in plugin_specs:
        if _is_local_plugin_spec(spec):
            with rich.status.Status(f"resolving {spec}", console=stderr_console):
                local_roots[spec] = _resolve_plugin_bytes(spec, None)

    resolution = _BundleResolution(root_names={}, closures={})
    for target in pip_targets:
        roots: dict[str, bytes] = {}
        for spec in plugin_specs:
            if spec in local_roots:
                name, buf = local_roots[spec]
                _validate_local_root_python(buf, target)
            else:
                with rich.status.Status(f"resolving {spec} for {target.id}", console=stderr_console):
                    try:
                        name, buf = _resolve_plugin_bytes(spec, repo, target.ida_platform, target.python_version)
                    except KeyError as e:
                        raise RuntimeError(f"cannot resolve '{spec}' for {target.id}: {e}") from e
            resolution.root_names[spec] = name
            roots[name] = buf

        with rich.status.Status(f"resolving loose dependencies for {target.id}", console=stderr_console):
            dependencies = _resolve_loose_deps(roots, repo, target.ida_platform, target.python_version)
        resolution.closures[target] = {**roots, **dependencies}

    return resolution


def _validate_local_root_python(buf: bytes, target: PipTarget) -> None:
    """Check that a local plugin archive's requiresPython allows the cell's Python version.

    Raises:
        RuntimeError: when requiresPython excludes the cell.
    """
    _, metadata = find_root_manifest_in_archive(buf)
    requirement = metadata.plugin.requires_python
    if requirement is not None and not is_python_version_compatible(target.python_version, requirement):
        raise RuntimeError(
            f"{metadata.plugin.name} {metadata.plugin.version} requires Python {requirement}, "
            f"which excludes target {target.id}"
        )


def _get_python_deps(closure: dict[str, bytes]) -> list[str]:
    """Collect the Python dependencies of every plugin in a cell's closure, without duplicates."""
    deps = (dep for buf in closure.values() for dep in _collect_all_python_deps(buf))
    return list(dict.fromkeys(deps))


def _stage_plugin_archives(
    name: str,
    bufs_by_target: dict[PipTarget, bytes],
    plugins_dir: Path,
    plugin_index: PluginArchiveIndex,
) -> None:
    """Write the distinct archives of one plugin into the staging directory and index them.

    Archive filenames get a platform suffix when the target cells resolved to different archives,
    or a target ID suffix when the platform suffix does not give each archive its own filename.

    Raises:
        ValueError: when two different archives map to the same filename.
    """
    targets_by_hash: dict[str, list[PipTarget]] = {}
    buf_by_hash: dict[str, bytes] = {}
    for target, buf in bufs_by_target.items():
        h = hashlib.sha256(buf).hexdigest()
        targets_by_hash.setdefault(h, []).append(target)
        buf_by_hash[h] = buf

    versions_by_hash = {h: get_version_from_plugin_archive(buf, name) for h, buf in buf_by_hash.items()}
    filenames_by_hash: dict[str, str] = {}
    for h, version in versions_by_hash.items():
        if len(buf_by_hash) > 1:
            suffix = "+".join(sorted({t.ida_platform for t in targets_by_hash[h]}))
            filenames_by_hash[h] = f"{name}-{version}-{suffix}.zip"
        else:
            filenames_by_hash[h] = f"{name}-{version}.zip"

    filenames = list(filenames_by_hash.values())
    for h, filename in filenames_by_hash.items():
        if filenames.count(filename) > 1:
            suffix = "+".join(sorted(t.id for t in targets_by_hash[h]))
            filenames_by_hash[h] = f"{name}-{versions_by_hash[h]}-{suffix}.zip"

    for h, buf in buf_by_hash.items():
        archive_filename = filenames_by_hash[h]
        version = versions_by_hash[h]
        dest = plugins_dir / archive_filename
        if not dest.exists():
            dest.write_bytes(buf)
        elif dest.read_bytes() != buf:
            raise ValueError(f"different archives for {name} {version} map to the same bundle path: {archive_filename}")
        plugin_index.index_plugin_archive(buf, f"hcli-bundle:plugins/{archive_filename}")


def _render_versions_by_target(
    name: str,
    bufs_by_target: dict[PipTarget, bytes],
    pip_targets: list[PipTarget],
) -> str:
    """Render the plugin version selected for each target cell, like ``2.0.0 (linux-x86_64), 1.0.0 (windows-x86_64)``.

    Renders only the version when a single version covers every target cell.
    A platform name stands for all target cells of that platform.
    """
    targets_by_version: dict[str, list[PipTarget]] = {}
    for target in pip_targets:
        if target in bufs_by_target:
            version = get_version_from_plugin_archive(bufs_by_target[target], name)
            targets_by_version.setdefault(version, []).append(target)

    if len(targets_by_version) == 1 and len(bufs_by_target) == len(pip_targets):
        return next(iter(targets_by_version))

    return ", ".join(
        f"{version} ({', '.join(_get_target_labels(targets, pip_targets))})"
        for version, targets in targets_by_version.items()
    )


def _get_target_labels(targets: list[PipTarget], pip_targets: list[PipTarget]) -> list[str]:
    labels: list[str] = []
    for target in targets:
        platform_targets = [t for t in pip_targets if t.ida_platform == target.ida_platform]
        if all(t in targets for t in platform_targets):
            if target.ida_platform not in labels:
                labels.append(target.ida_platform)
        else:
            labels.append(target.id)
    return labels


def _collect_all_python_deps(buf: bytes) -> list[str]:
    """Collect python dependencies from a plugin archive, including components and inline PEP 723."""
    deps: list[str] = []
    for _, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
        deps.extend(get_python_dependencies_from_plugin_archive(buf, metadata))
    return deps


def _resolve_loose_deps(
    known_archives: dict[str, bytes],
    plugin_repo: BasePluginRepo | None,
    platform: str,
    python_version: str | None = None,
) -> dict[str, bytes]:
    """Resolve loose plugin dependencies recursively for one platform and Python version.

    A dependency without a version pin resolves to the newest version that supports the platform
    and whose requiresPython allows the Python version.

    Returns new archives, by plugin name, not already in known_archives.

    Raises:
        RuntimeError: when a required dependency cannot be resolved.
    """
    if plugin_repo is None:
        return {}

    resolved: dict[str, bytes] = {}
    seen_names: set[str] = {name.lower() for name in known_archives}
    queue: list[bytes] = list(known_archives.values())
    depth = 0

    while queue and depth < MAX_LOOSE_DEP_DEPTH:
        next_queue: list[bytes] = []
        for buf in queue:
            for _, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
                for entry in metadata.plugin.dependencies:
                    if not isinstance(entry, DependencyEntry):
                        continue
                    dep_name = entry.reference.name
                    if dep_name.lower() in seen_names:
                        continue
                    seen_names.add(dep_name.lower())

                    spec = entry.format_spec()
                    try:
                        # key by the repository's plugin name: lookup ignores case, so the declared name may differ
                        resolved_name, dep_buf = plugin_repo.fetch_plugin_from_spec(
                            f"{dep_name}{entry.reference.version_spec}",
                            platform,
                            host=entry.reference.host,
                            python_version=python_version,
                        )
                    except Exception as e:
                        dependent = f"{metadata.plugin.name} {metadata.plugin.version}"
                        if entry.required:
                            raise RuntimeError(
                                f"{dependent} requires '{spec}', which cannot be resolved for {platform}: {e}"
                            ) from e
                        logger.warning(
                            "skipping optional dependency '%s' of %s for %s: %s", spec, dependent, platform, e
                        )
                        continue

                    resolved[resolved_name] = dep_buf
                    next_queue.append(dep_buf)

        queue = next_queue
        depth += 1

    return resolved


def _download_wheelhouse(
    deps: list[str],
    target: PipTarget,
    dest: Path,
    pip_options: PipOptions,
) -> None:
    python_exe = find_current_python_executable()
    cmd = [
        str(python_exe),
        "-m",
        "pip",
        "download",
        *target.pip_download_args(),
        "--dest",
        str(dest),
    ]

    if pip_options.index_url:
        cmd.extend(["--index-url", pip_options.index_url])
    for url in pip_options.extra_index_urls:
        cmd.extend(["--extra-index-url", url])
    for link in pip_options.find_links:
        cmd.extend(["--find-links", str(link)])
    if pip_options.offline:
        cmd.append("--no-index")

    cmd.extend(deps)

    logger.debug("pip download: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace")
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        raise RuntimeError(f"pip download failed for target {target.id}:\n{stdout_text}\n{stderr_text}")


def _verify_wheelhouse(wh_dir: Path, target: PipTarget) -> None:
    for f in wh_dir.iterdir():
        if f.suffix == ".whl":
            continue
        if f.name.endswith((".tar.gz", ".tar.bz2", ".zip")):
            raise ValueError(f"sdist found in wheelhouse for {target.id}: {f.name}")


def _write_bundle_zip(output: Path, manifest_bytes: bytes, staging: Path) -> None:
    tmp_output = output.with_suffix(".tmp.zip")
    try:
        with zipfile.ZipFile(tmp_output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("plugin-bundle.json", manifest_bytes)

            for file_path in sorted(staging.rglob("*")):
                if file_path.is_file():
                    arcname = file_path.relative_to(staging).as_posix()
                    zf.write(file_path, arcname)

        shutil.move(str(tmp_output), str(output))
    except Exception:
        tmp_output.unlink(missing_ok=True)
        raise
