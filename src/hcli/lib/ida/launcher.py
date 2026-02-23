"""IDA launcher with robust startup handling.

This module provides functionality to launch IDA with an IDB file
and wait for it to become ready via IPC.
"""

from __future__ import annotations

import getpass
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hcli.lib.config import config_store
from hcli.lib.ida import (
    MissingCurrentInstallationDirectory,
    find_current_ida_install_directory,
    get_ida_binary_path,
)
from hcli.lib.ida.ipc import IDAInstance, IDAIPCClient

logger = logging.getLogger(__name__)


class IDALaunchError(Exception):
    """Failed to launch IDA process."""

    def __init__(self, message: str, exit_code: int | None = None):
        self.exit_code = exit_code
        super().__init__(message)


class IDAStartupTimeout(Exception):
    """IDA took too long to become responsive."""

    def __init__(self, timeout: float, phase: str):
        self.timeout = timeout
        self.phase = phase
        super().__init__(f"IDA startup timeout after {timeout}s during {phase}")


class IDBNotFoundError(Exception):
    """IDB file not found in sources."""

    def __init__(self, idb_filename: str, sources: dict[str, str]):
        self.idb_filename = idb_filename
        self.sources = sources
        super().__init__(f"IDB '{idb_filename}' not found in sources")


class NoIDAInstallationError(Exception):
    """No IDA installation configured or found."""


@dataclass
class LaunchConfig:
    """Configuration for IDA launch behavior."""

    socket_timeout: float = 30.0
    idb_loaded_timeout: float = 90.0
    initial_poll_interval: float = 0.1
    max_poll_interval: float = 2.0
    backoff_multiplier: float = 1.5
    skip_analysis_wait: bool = False
    analysis_poll_interval: float = 5.0  # seconds between analysis polls


@dataclass
class LaunchResult:
    """Result of an IDA launch attempt."""

    success: bool
    instance: IDAInstance | None = None
    process: subprocess.Popen | None = None
    error_message: str | None = None


class IDALauncher:
    """Manages IDA process lifecycle with robust error handling."""

    SOCKET_PREFIX = "ida_ipc_"

    def __init__(self, config: LaunchConfig | None = None):
        self.config = config or LaunchConfig()

    def find_idb_file(self, idb_filename: str, source_name: str = "") -> Path | None:
        """Search configured sources for the IDB file.

        Args:
            idb_filename: The IDB filename to search for (e.g., "test.idb", "sample.i64")
            source_name: If non-empty and not "localhost", search only this source's directory.
                         If empty or "localhost", search all source directories.

        Returns:
            Full path to the IDB file if found, None otherwise.
        """
        sources = self._get_sources()

        if not sources:
            logger.debug("No sources configured")
            return None

        # Determine which directories to search
        if source_name and source_name != "localhost":
            if source_name not in sources:
                logger.debug(f"Source '{source_name}' not found in configuration")
                return None
            dirs_to_search = {source_name: sources[source_name]}
        else:
            dirs_to_search = sources

        for name, dir_path in dirs_to_search.items():
            dir_path_obj = Path(dir_path)
            if not dir_path_obj.exists():
                logger.debug(f"Source '{name}' path does not exist: {dir_path}")
                continue

            # Search recursively for matching filename
            for idb_path in dir_path_obj.rglob(idb_filename):
                if idb_path.is_file():
                    logger.debug(f"Found IDB in source '{name}': {idb_path}")
                    return idb_path

        logger.debug(f"IDB '{idb_filename}' not found in any source")
        return None

    def _get_sources(self) -> dict[str, str]:
        """Get sources from config, migrating from legacy search-paths if needed."""
        sources: dict[str, str] = config_store.get_object("idb.sources", {}) or {}

        if not sources and config_store.has("idb.search-paths"):
            # Migrate legacy search-paths to named sources
            search_paths: list[str] = config_store.get_object("idb.search-paths", []) or []
            if search_paths:
                logger.info("Migrating idb.search-paths to idb.sources")
                for i, path in enumerate(search_paths, 1):
                    sources[f"source-{i}"] = path
                config_store.set_object("idb.sources", sources)
                config_store.remove_string("idb.search-paths")

        return sources

    def get_ida_binary(self) -> Path:
        """Get IDA binary from ida.default/ida.instances config.

        Returns:
            Path to the IDA binary.

        Raises:
            NoIDAInstallationError: If no IDA installation is configured or found.
        """
        # Try ida.instances configuration first
        default_instance = config_store.get_string("ida.default", "")
        instances: dict[str, str] = config_store.get_object("ida.instances", {}) or {}

        if default_instance and default_instance in instances:
            ida_dir = Path(instances[default_instance])
            ida_bin = get_ida_binary_path(ida_dir)
            if ida_bin.exists():
                logger.debug(f"Using configured IDA: {ida_bin}")
                return ida_bin
            else:
                logger.warning(f"Configured IDA binary not found: {ida_bin}")

        # Fallback to standard discovery
        try:
            ida_dir = find_current_ida_install_directory()
            ida_bin = get_ida_binary_path(ida_dir)
            if ida_bin.exists():
                logger.debug(f"Using discovered IDA: {ida_bin}")
                return ida_bin
        except MissingCurrentInstallationDirectory:
            pass

        raise NoIDAInstallationError("No IDA installation configured. Use: hcli ida instance add --auto")

    def launch_and_wait(
        self,
        idb_path: Path,
        timeout: float | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> LaunchResult:
        """Launch IDA with an IDB file and wait for it to be ready.

        Args:
            idb_path: Full path to the IDB file.
            timeout: Total timeout in seconds (default: socket_timeout + idb_loaded_timeout).
            progress_callback: Optional callback for progress messages.

        Returns:
            LaunchResult with success status and instance info.
        """

        def report(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)
            logger.info(msg)

        # Validate IDB file exists
        if not idb_path.exists():
            return LaunchResult(success=False, error_message=f"IDB file not found: {idb_path}")

        if not idb_path.is_file():
            return LaunchResult(success=False, error_message=f"IDB path is not a file: {idb_path}")

        # Get IDA binary
        try:
            ida_bin = self.get_ida_binary()
        except NoIDAInstallationError as e:
            return LaunchResult(success=False, error_message=str(e))

        # Launch IDA process
        # On macOS, use 'open -a' to launch via LaunchServices, which escapes
        # any sandbox restrictions from protocol handlers
        if sys.platform == "darwin":
            # Extract .app bundle path from binary path
            # e.g., /Applications/IDA.app/Contents/MacOS/ida -> /Applications/IDA.app
            ida_bin_str = str(ida_bin)
            if "/Contents/MacOS/" in ida_bin_str:
                app_bundle = ida_bin_str.split("/Contents/MacOS/")[0]
                # Use --args to pass the IDB path as an argument to the app
                cmd = ["open", "-a", app_bundle, "--args", str(idb_path)]
            else:
                cmd = [ida_bin_str, str(idb_path)]
        else:
            cmd = [str(ida_bin), str(idb_path)]

        report(f"Command: {' '.join(cmd)}")
        report(f"User: {getpass.getuser()}, CWD: {os.getcwd()}")

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            return LaunchResult(success=False, error_message=f"Failed to start IDA: {e}")

        # Calculate timeout
        total_timeout = (
            timeout if timeout is not None else (self.config.socket_timeout + self.config.idb_loaded_timeout)
        )

        # Wait for IDA instance with our IDB to appear
        target_idb_name = idb_path.name
        report(f"Waiting for IDA to open {target_idb_name}...")
        try:
            instance = self._wait_for_idb_instance(target_idb_name, total_timeout)
        except IDAStartupTimeout as e:
            return LaunchResult(success=False, error_message=str(e))

        # Wait for auto-analysis to complete (unless skipped)
        if not self.config.skip_analysis_wait:
            report("Waiting for auto-analysis to complete (Ctrl+C to skip)...")
            try:
                self._wait_for_analysis_on_instance(instance.socket_path, report)
            except IDALaunchError as e:
                return LaunchResult(success=False, error_message=str(e))
            except KeyboardInterrupt:
                report("Analysis wait cancelled by user")

        report("IDA is ready!")
        return LaunchResult(success=True, instance=instance)

    def _get_expected_socket_path(self, pid: int) -> str:
        """Get expected IPC socket/pipe path for a PID."""
        if sys.platform == "win32":
            return f"\\\\.\\pipe\\{self.SOCKET_PREFIX}{pid}"
        else:
            return f"/tmp/{self.SOCKET_PREFIX}{pid}"

    def _socket_exists(self, socket_path: str) -> bool:
        """Check if socket/pipe exists."""
        if sys.platform == "win32":
            return self._windows_pipe_exists(socket_path)
        else:
            return Path(socket_path).exists()

    def _windows_pipe_exists(self, pipe_path: str) -> bool:
        """Check if Windows named pipe exists."""
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.CreateFileW(
                pipe_path,
                0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
                0,
                None,
                3,  # OPEN_EXISTING
                0,
                None,
            )
            if handle != -1:
                kernel32.CloseHandle(handle)
                return True
        except OSError:
            return False
        else:
            return False

    def _wait_for_socket_responsive(self, process: subprocess.Popen, socket_path: str, timeout: float) -> None:
        """Wait for socket to exist and respond to ping."""
        start = time.monotonic()
        interval = self.config.initial_poll_interval

        while time.monotonic() - start < timeout:
            # Check process health
            exit_code = process.poll()
            if exit_code is not None:
                raise IDALaunchError(f"IDA exited unexpectedly with code {exit_code}", exit_code=exit_code)

            # Check if socket exists and responds to ping
            if self._socket_exists(socket_path) and IDAIPCClient.ping(socket_path):
                return

            time.sleep(interval)
            interval = min(interval * self.config.backoff_multiplier, self.config.max_poll_interval)

        raise IDAStartupTimeout(timeout, "socket_responsive")

    def _wait_for_idb_loaded(self, process: subprocess.Popen, socket_path: str, timeout: float) -> IDAInstance:
        """Wait for IDB to be loaded and return instance info."""
        start = time.monotonic()
        interval = self.config.initial_poll_interval

        while time.monotonic() - start < timeout:
            # Check process health
            exit_code = process.poll()
            if exit_code is not None:
                raise IDALaunchError(f"IDA exited unexpectedly with code {exit_code}", exit_code=exit_code)

            # Query instance for IDB info
            info = IDAIPCClient.query_instance(socket_path)
            if info and info.has_idb:
                return info

            time.sleep(interval)
            interval = min(interval * self.config.backoff_multiplier, self.config.max_poll_interval)

        raise IDAStartupTimeout(timeout, "idb_loaded")

    def _wait_for_analysis(
        self,
        process: subprocess.Popen,
        socket_path: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Wait for auto-analysis to complete by polling.

        Polls is_analysis_complete at regular intervals with process health
        checks. User can cancel with Ctrl+C.
        """
        start = time.monotonic()

        while True:
            # Check process health
            exit_code = process.poll()
            if exit_code is not None:
                raise IDALaunchError(f"IDA exited unexpectedly with code {exit_code}", exit_code=exit_code)

            result = IDAIPCClient.is_analysis_complete(socket_path)

            if result.success:
                elapsed = time.monotonic() - start
                if progress_callback:
                    progress_callback(f"Analysis complete ({elapsed:.1f}s)")
                return

            if result.status == "error":
                raise IDALaunchError(f"Analysis check error: {result.message}")

            # Not complete yet, sleep and retry
            elapsed = time.monotonic() - start
            if progress_callback:
                progress_callback(f"Waiting for analysis... ({elapsed:.0f}s)")

            time.sleep(self.config.analysis_poll_interval)

    @staticmethod
    def _strip_idb_extension(name: str) -> str:
        """Strip .i64 or .idb extension from filename."""
        lower = name.lower()
        if lower.endswith((".i64", ".idb")):
            return name[:-4]
        return name

    def _idb_names_match(self, ida_idb_name: str, target_name: str) -> bool:
        """Check if IDA's IDB name matches the target name.

        Handles the case where target is 'foo.bin' but IDA reports 'foo.bin.i64'.
        """
        # Strip IDB extensions and compare
        ida_base = self._strip_idb_extension(ida_idb_name).lower()
        target_base = self._strip_idb_extension(target_name).lower()
        return ida_base == target_base

    def _wait_for_idb_instance(self, idb_name: str, timeout: float) -> IDAInstance:
        """Wait for an IDA instance with the specified IDB to appear.

        Polls all IDA IPC sockets looking for one with the matching IDB.
        """
        start = time.monotonic()
        interval = self.config.initial_poll_interval

        while time.monotonic() - start < timeout:
            # Discover all IDA instances
            instances = IDAIPCClient.discover_instances()
            for instance in instances:
                info = IDAIPCClient.query_instance(instance.socket_path)
                if info and info.has_idb and info.idb_name and self._idb_names_match(info.idb_name, idb_name):
                    return info

            time.sleep(interval)
            interval = min(interval * self.config.backoff_multiplier, self.config.max_poll_interval)

        raise IDAStartupTimeout(timeout, "waiting for IDB instance")

    def _wait_for_analysis_on_instance(
        self,
        socket_path: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Wait for auto-analysis to complete by polling.

        Similar to _wait_for_analysis but without process health checks
        (used when we launched via 'open -a' and don't have a process handle).
        """
        start = time.monotonic()

        while True:
            result = IDAIPCClient.is_analysis_complete(socket_path)

            if result.success:
                elapsed = time.monotonic() - start
                if progress_callback:
                    progress_callback(f"Analysis complete ({elapsed:.1f}s)")
                return

            if result.status == "error":
                raise IDALaunchError(f"Analysis check error: {result.message}")

            # Not complete yet, sleep and retry
            elapsed = time.monotonic() - start
            if progress_callback:
                progress_callback(f"Waiting for analysis... ({elapsed:.0f}s)")

            time.sleep(self.config.analysis_poll_interval)
