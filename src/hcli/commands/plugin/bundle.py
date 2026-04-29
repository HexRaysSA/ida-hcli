from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import rich.status
import rich_click as click

from hcli import __version__ as hcli_version
from hcli.lib.console import _sync_console_streams, console, stderr_console
from hcli.lib.ida.plugin import (
    get_metadatas_with_paths_from_plugin_archive,
    get_version_from_plugin_archive,
)
from hcli.lib.ida.plugin.bundle import (
    ALL_PLATFORMS,
    SUPPORTED_PYTHON_VERSIONS,
    PipTarget,
    resolve_platform_alias,
    to_manifest_target,
)
from hcli.lib.ida.plugin.reference import parse_plugin_reference
from hcli.lib.ida.plugin.repo import PluginArchiveIndex
from hcli.lib.ida.plugin.repo.bundle import (
    PluginBundleRepo,
    is_plugin_bundle_zip,
)
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions, find_current_python_executable

logger = logging.getLogger(__name__)


@click.group()
def bundle() -> None:
    """Manage plugin bundles for offline installation."""
    _sync_console_streams()


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


def _resolve_plugin_bytes(
    spec: str,
    pip_options: PipOptions,
    plugin_repo=None,
    current_platform: str | None = None,
) -> tuple[str, bytes]:
    path = Path(spec)
    if path.exists() and spec.endswith(".zip"):
        buf = path.read_bytes()
        items = list(get_metadatas_with_paths_from_plugin_archive(buf))
        if not items:
            raise ValueError(f"no ida-plugin.json found in {spec}")
        return items[0][1].plugin.name, buf

    host: str | None = None
    clean_spec = spec
    try:
        ref = parse_plugin_reference(spec)
        host = ref.host
        clean_spec = f"{ref.name}{ref.version_spec}" if ref.version_spec else ref.name
    except ValueError:
        pass

    if "==" not in clean_spec:
        raise click.BadParameter(f"repository plugin specs must include exact version (e.g. {spec}==1.0.0)")

    if plugin_repo is None:
        raise click.BadParameter("no plugin repository available to resolve spec")

    return plugin_repo.fetch_plugin_from_spec(clean_spec, current_platform, host=host)


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

            resolved_pythons.append(detect_current_python_version())
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
    """Create a plugin bundle from plugin specs and/or local ZIPs."""
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

    current_platform: str | None = None

    with tempfile.TemporaryDirectory(prefix="hcli-bundle-staging-") as staging_dir:
        staging = Path(staging_dir)
        plugins_dir = staging / "plugins"
        plugins_dir.mkdir()
        deps_dir = staging / "dependencies" / "python"
        deps_dir.mkdir(parents=True)

        all_python_deps: list[str] = []
        plugin_index = PluginArchiveIndex()

        for spec in plugin_specs:
            if not (Path(spec).exists() and spec.endswith(".zip")) and current_platform is None:
                from hcli.lib.ida import find_current_ida_platform

                current_platform = find_current_ida_platform()

            with rich.status.Status(f"resolving {spec}", console=stderr_console):
                name, buf = _resolve_plugin_bytes(spec, pip_options, parent_repo, current_platform)

            archive_filename = f"{name}-{get_version_from_plugin_archive(buf, name)}.zip"
            dest = plugins_dir / archive_filename
            if dest.exists():
                logger.debug("plugin archive already staged: %s", archive_filename)
            else:
                dest.write_bytes(buf)

            plugin_index.index_plugin_archive(buf, f"hcli-bundle:plugins/{archive_filename}")

            for _, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
                if isinstance(metadata.plugin.python_dependencies, list):
                    all_python_deps.extend(metadata.plugin.python_dependencies)

        target_manifests = []
        if all_python_deps:
            for target in pip_targets:
                wh_rel = f"dependencies/python/{target.id}"
                wh_dir = deps_dir / target.id
                wh_dir.mkdir(parents=True, exist_ok=True)

                with rich.status.Status(
                    f"downloading wheels for {target.ida_platform} Python {target.python_version}",
                    console=stderr_console,
                ):
                    _download_wheelhouse(all_python_deps, target, wh_dir, pip_options)

                _verify_wheelhouse(wh_dir, target)
                target_manifests.append(to_manifest_target(target, wh_rel))
        else:
            for target in pip_targets:
                wh_rel = f"dependencies/python/{target.id}"
                wh_dir = deps_dir / target.id
                wh_dir.mkdir(parents=True, exist_ok=True)
                target_manifests.append(to_manifest_target(target, wh_rel))

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
    console.print(f"  plugins: {len(plugin_specs)}")
    console.print(f"  targets: {len(pip_targets)}")
    for t in pip_targets:
        console.print(f"    {t.ida_platform}  Python {t.python_version}")


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
