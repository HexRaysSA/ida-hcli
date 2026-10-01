from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import rich.status
import rich_click as click

from hcli import __version__ as hcli_version
from hcli.lib.console import console, stderr_console
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    get_metadatas_with_paths_from_plugin_archive,
    get_python_dependencies_from_plugin_archive,
    get_version_from_plugin_archive,
    is_python_version_compatible,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.bundle import (
    ALL_PLATFORMS,
    SUPPORTED_PYTHON_VERSIONS,
    PipTarget,
    resolve_platform_alias,
    to_manifest_target,
)
from hcli.lib.ida.plugin.components import find_root_manifest_in_archive
from hcli.lib.ida.plugin.exceptions import AmbiguousPluginReferenceError
from hcli.lib.ida.plugin.reference import format_qualified_plugin_reference, parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo, PluginArchiveIndex
from hcli.lib.ida.plugin.repo.bundle import (
    PluginBundleRepo,
    is_plugin_bundle_zip,
)
from hcli.lib.ida.plugin.repo.scoped import ScopedPluginRepo
from hcli.lib.ida.plugin.resolve import Cell, Requirement, ResolutionError, SkippedRequirement, resolve
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


def _read_local_plugin(spec: str) -> tuple[str, bytes]:
    """Read the archive of a local plugin directory or ZIP, and the plugin's name."""
    path = Path(spec).expanduser()

    if path.is_dir():
        from hcli.lib.ida.plugin.install import pack_plugin_directory_to_zip

        buf = pack_plugin_directory_to_zip(path.resolve())
    else:
        buf = path.read_bytes()
    _, meta = find_root_manifest_in_archive(buf)
    return meta.plugin.name, buf


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
    root_hosts = _get_root_hosts(ctx, plugin_specs, bundle_repo)

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

        resolution = _get_cell_closures(plugin_specs, pip_targets, parent_repo, root_hosts)

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

        for (chain, reason), skipped_targets in _group_skipped_by_target(resolution.skipped, pip_targets).items():
            labels = ", ".join(_get_target_labels(skipped_targets, pip_targets))
            stderr_console.print(
                f"[yellow]warning[/yellow]: skipped optional dependency {' -> '.join(chain)} ({labels}): {reason}"
            )

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


@dataclass
class _BundleResolution:
    """The plugins selected for each target cell.

    Attributes:
        root_names: plugin name for each positional spec.
        closures: plugin archives by plugin name, for each target cell.
        skipped: optional dependencies that could not be resolved, for each target cell.
    """

    root_names: dict[str, str]
    closures: dict[PipTarget, dict[str, bytes]]
    skipped: dict[PipTarget, list[SkippedRequirement]] = field(default_factory=dict)


def _get_root_hosts(ctx: click.Context, plugin_specs: tuple[str, ...], bundle_repo: str | None) -> dict[str, str]:
    """Find the host of the plugin that each positional spec with a `repo/` prefix names in that repository.

    Returns:
        the plugin host, by spec.

    Raises:
        click.Abort: when a prefix is combined with --repo, names an unknown or unreachable repository,
            or names a plugin that the repository does not list or lists from several hosts.
    """
    from hcli.commands.plugin import repo_for_reference

    hosts: dict[str, str] = {}
    for spec in plugin_specs:
        if _is_local_plugin_spec(spec):
            continue
        try:
            ref = parse_plugin_reference(spec)
        except ValueError:
            continue
        if ref.repo is None:
            continue
        if bundle_repo is not None:
            console.print(
                f"[red]Cannot use the repository prefix '{ref.repo}/' with --repo[/red]: "
                f"--repo already selects the only repository searched."
            )
            raise click.Abort()
        try:
            plugin = repo_for_reference(ctx, ref).get_plugin_by_name(ref.name, host=ref.host)
        except KeyError:
            console.print(f"[red]Plugin {ref.name} is not in repository {ref.repo}[/red]")
            raise click.Abort()
        except AmbiguousPluginReferenceError as e:
            console.print(f"[red]Plugin name '{ref.name}' is ambiguous in repository {ref.repo}[/red]")
            console.print("Choose one of:")
            for candidate_ref in e.candidate_refs:
                console.print(f"  {ref.repo}/{format_qualified_plugin_reference(candidate_ref)}")
            raise click.Abort()
        hosts[spec] = plugin.host
    return hosts


def _get_local_metadata(buf: bytes) -> IDAMetadataDescriptor:
    """Read a local plugin's metadata, with its components expanded as a repository index lists them.

    Raises:
        ValueError: when the archive does not contain a valid plugin.
    """
    # The index drops an invalid archive without a reason, so validate first to report the cause.
    for path, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
        validate_metadata_in_plugin_archive(buf, path, metadata)

    index = PluginArchiveIndex()
    index.index_plugin_archive(buf, "local")
    plugins = index.get_plugins()
    if not plugins:
        _, metadata = find_root_manifest_in_archive(buf)
        raise ValueError(f"{metadata.plugin.name} {metadata.plugin.version} is not a valid plugin archive")
    [locations] = plugins[0].versions.values()
    return locations[0].metadata


def _get_cell_closures(
    plugin_specs: tuple[str, ...],
    pip_targets: list[PipTarget],
    plugin_repo: BasePluginRepo | None,
    root_hosts: dict[str, str] | None = None,
) -> _BundleResolution:
    """Resolve the plugin specs and their loose dependencies separately for each target cell.

    Local specs are fixed roots. Repository specs are resolved in order, newest viable version first.
    A spec in `root_hosts` selects only the plugin with that host.

    Raises:
        click.BadParameter: when a repository spec is given without a plugin repository.
        RuntimeError: when a plugin spec or a required dependency cannot be resolved for a cell,
            or when a local plugin's requiresPython excludes a cell.
        ValueError: when a local plugin archive is not valid.
    """
    local_roots: dict[str, tuple[str, bytes]] = {}
    fixed: dict[str, IDAMetadataDescriptor] = {}
    roots: dict[str, Requirement] = {}
    for spec in plugin_specs:
        if _is_local_plugin_spec(spec):
            with rich.status.Status(f"resolving {spec}", console=stderr_console):
                name, buf = _read_local_plugin(spec)
            local_roots[spec] = (name, buf)
            fixed[name] = _get_local_metadata(buf)
        else:
            try:
                requirement = Requirement.from_spec(spec)
            except ValueError as e:
                raise click.BadParameter(f"invalid plugin spec '{spec}': {e}") from e
            if root_hosts and spec in root_hosts:
                requirement = replace(requirement, host=root_hosts[spec])
            roots[spec] = requirement

    if roots and plugin_repo is None:
        raise click.BadParameter("no plugin repository available to resolve spec")
    repo = ScopedPluginRepo(plugin_repo)

    resolution = _BundleResolution(root_names={}, closures={})
    for spec, (name, _) in local_roots.items():
        resolution.root_names[spec] = name

    for target in pip_targets:
        for metadata in fixed.values():
            _validate_local_root_python(metadata, target)

        cell = Cell(target.ida_platform, python_version=target.python_version, label=target.id)
        with rich.status.Status(f"resolving plugins for {target.id}", console=stderr_console):
            try:
                cell_resolution = resolve(list(roots.values()), repo, cell, fixed=fixed)
            except ResolutionError as e:
                raise RuntimeError(str(e)) from e

            closure = dict(local_roots.values())
            for name, location in cell_resolution.selected.items():
                closure[name] = repo.fetch_plugin_location(location)[1]

        for spec, requirement in roots.items():
            resolution.root_names[spec] = cell_resolution.roots[requirement]
        resolution.closures[target] = closure
        resolution.skipped[target] = cell_resolution.skipped

    return resolution


def _group_skipped_by_target(
    skipped: dict[PipTarget, list[SkippedRequirement]],
    pip_targets: list[PipTarget],
) -> dict[tuple[tuple[str, ...], str], list[PipTarget]]:
    """Group the skipped optional dependencies by chain and reason, keeping target cell order."""
    grouped: dict[tuple[tuple[str, ...], str], list[PipTarget]] = {}
    for target in pip_targets:
        for item in skipped.get(target, []):
            grouped.setdefault((item.chain, item.reason), []).append(target)
    return grouped


def _validate_local_root_python(metadata: IDAMetadataDescriptor, target: PipTarget) -> None:
    """Check that a local plugin's requiresPython allows the cell's Python version.

    Raises:
        RuntimeError: when requiresPython excludes the cell.
    """
    requires_python = metadata.plugin.requires_python
    if requires_python is not None and not is_python_version_compatible(target.python_version, requires_python):
        raise RuntimeError(
            f"{metadata.plugin.name} {metadata.plugin.version} requires Python {requires_python}, "
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

    Renders only the version when a single archive covers every target cell.
    Each distinct archive is listed separately, even when two archives have the same version.
    A platform name stands for all target cells of that platform.
    """
    targets_by_hash: dict[str, list[PipTarget]] = {}
    version_by_hash: dict[str, str] = {}
    for target in pip_targets:
        if target in bufs_by_target:
            buf = bufs_by_target[target]
            h = hashlib.sha256(buf).hexdigest()
            targets_by_hash.setdefault(h, []).append(target)
            if h not in version_by_hash:
                version_by_hash[h] = get_version_from_plugin_archive(buf, name)

    if len(targets_by_hash) == 1 and len(bufs_by_target) == len(pip_targets):
        return next(iter(version_by_hash.values()))

    return ", ".join(
        f"{version_by_hash[h]} ({', '.join(_get_target_labels(targets, pip_targets))})"
        for h, targets in targets_by_hash.items()
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
