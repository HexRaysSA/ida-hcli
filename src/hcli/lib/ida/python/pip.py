"""Drive pip against a given Python interpreter."""

import logging
import os
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def does_current_ida_have_pip(python_exe: Path, timeout=10.0) -> bool:
    """Check if pip is available in the given Python executable."""
    try:
        process = subprocess.run(
            [str(python_exe), "-c", "import pip"], capture_output=True, timeout=timeout, check=False
        )
        return process.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


class CantInstallPackagesError(ValueError): ...


@dataclass(frozen=True)
class PipOptions:
    index_url: str | None = None
    extra_index_urls: tuple[str, ...] = ()
    find_links: tuple[Path | str, ...] = ()
    offline: bool = False
    isolated: bool = False
    no_cache_dir: bool = False
    disable_pip_version_check: bool = False
    no_build_isolation: bool = False

    @property
    def has_custom_sources(self) -> bool:
        return self.index_url is not None or len(self.extra_index_urls) > 0 or len(self.find_links) > 0

    def build_args(self) -> list[str]:
        args: list[str] = []
        if self.isolated:
            args.append("--isolated")
        if self.disable_pip_version_check:
            args.append("--disable-pip-version-check")
        if self.no_cache_dir:
            args.append("--no-cache-dir")
        if self.offline:
            args.append("--no-index")
        if self.index_url:
            args.extend(["--index-url", self.index_url])
        for url in self.extra_index_urls:
            args.extend(["--extra-index-url", url])
        for link in self.find_links:
            args.extend(["--find-links", str(link)])
        if self.no_build_isolation:
            args.append("--no-build-isolation")
        return args


PIP_OPTIONS_DEFAULT = PipOptions()


def merge_bundle_pip_options(user_options: PipOptions, bundle_options: PipOptions) -> PipOptions:
    return PipOptions(
        index_url=user_options.index_url,
        extra_index_urls=user_options.extra_index_urls,
        find_links=bundle_options.find_links + user_options.find_links,
        offline=bundle_options.offline or user_options.offline,
        isolated=bundle_options.isolated or user_options.isolated,
        no_cache_dir=bundle_options.no_cache_dir or user_options.no_cache_dir,
        disable_pip_version_check=bundle_options.disable_pip_version_check or user_options.disable_pip_version_check,
        no_build_isolation=user_options.no_build_isolation,
    )


def _format_pip_error(cmd: str, stdout: bytes, stderr: bytes) -> str:
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    parts = ["", cmd]
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append(stderr_text)

    return "\n".join(parts) if parts else stdout_text


def _printable_command(command: Sequence[str]) -> str:
    """Render a command so the user can copy-paste it into their shell."""
    # IDA install paths routinely contain spaces, so quote rather than plain-join.
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def run_pip(
    python_exe: Path,
    args: Sequence[str],
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run `python -m pip <args>` against the given interpreter, capturing output.

    The single place where pip is invoked: any subcommand (install, uninstall,
    freeze, download, ...) goes through here so failures are reported and logged
    the same way, with the command line included.

    Args:
        check: raise on a non-zero exit. Pass False when the caller treats a
            non-zero exit as data rather than an error.

    Raises:
        CantInstallPackagesError: if check is set and pip exits non-zero.
    """
    command = [str(python_exe), "-m", "pip", *args]
    printable = _printable_command(command)
    logger.debug("running pip: %s", printable)

    process = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
    if check and process.returncode != 0:
        logger.debug("pip failed with exit code %d", process.returncode)
        logger.debug(process.stdout.decode("utf-8", errors="replace"))
        logger.debug(process.stderr.decode("utf-8", errors="replace"))
        raise CantInstallPackagesError(_format_pip_error(printable, process.stdout, process.stderr))
    return process


def _run_pip_install(
    python_exe: Path,
    packages: list[str],
    pip_options: PipOptions,
    no_build_isolation: bool,
    dry_run: bool,
) -> None:
    effective = _merge_no_build_isolation(pip_options, no_build_isolation)
    args = ["install"]
    if dry_run:
        args.append("--dry-run")
    args += effective.build_args() + packages
    run_pip(python_exe, args)


def verify_pip_can_install_packages(
    python_exe: Path,
    packages: list[str],
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
    no_build_isolation: bool = False,
):
    """Check if the given Python packages (e.g., "foo>=v1.0,<3") can be installed.

    Raises:
        CantInstallPackagesError: if pip dry-run fails.
    """
    _run_pip_install(python_exe, packages, pip_options, no_build_isolation, dry_run=True)


def pip_install_packages(
    python_exe: Path,
    packages: list[str],
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
    no_build_isolation: bool = False,
):
    """Install the given Python packages (e.g., "foo>=v1.0,<3").

    Raises:
        CantInstallPackagesError: if pip install fails.
    """
    _run_pip_install(python_exe, packages, pip_options, no_build_isolation, dry_run=False)


def _merge_no_build_isolation(pip_options: PipOptions, no_build_isolation: bool) -> PipOptions:
    if no_build_isolation and not pip_options.no_build_isolation:
        return PipOptions(
            index_url=pip_options.index_url,
            extra_index_urls=pip_options.extra_index_urls,
            find_links=pip_options.find_links,
            offline=pip_options.offline,
            isolated=pip_options.isolated,
            no_cache_dir=pip_options.no_cache_dir,
            disable_pip_version_check=pip_options.disable_pip_version_check,
            no_build_isolation=True,
        )
    return pip_options


def pip_freeze(python_exe: Path):
    process = run_pip(python_exe, ["freeze"], check=False)
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, process.args, process.stdout, process.stderr)
    return process.stdout.decode("utf-8", errors="replace")
