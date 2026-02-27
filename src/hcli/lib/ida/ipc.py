"""IPC client for communicating with running IDA instances.

This module provides functionality to discover running IDA instances and
send commands to them via local sockets (Unix domain sockets on Linux/macOS,
named pipes on Windows).

Protocol: JSON-based request/response
  Request:  {"cmd": "ping|get_info|open_ida_link|is_analysis_complete", ...}
  Response: {"status": "ok|error", ...}

Socket naming: ida_ipc_<pid> (e.g., ida_ipc_12345)
"""

from __future__ import annotations

import glob
import json
import logging
import os
import socket
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IDAInstance:
    """Information about a running IDA instance."""

    pid: int
    socket_path: str
    idb_path: str | None = None
    idb_name: str | None = None
    has_idb: bool = False


@dataclass
class AnalysisResult:
    """Result of wait_for_analysis command."""

    success: bool  # True if analysis is complete
    status: str  # "ok", "timeout", "cancelled", "error"
    message: str | None = None


class IDAIPCError(Exception):
    """Base exception for IDA IPC errors."""


class IPCConnectionError(IDAIPCError):
    """Failed to connect to IDA instance."""


class IPCTimeoutError(IDAIPCError):
    """Connection or operation timed out."""


class IPCProtocolError(IDAIPCError):
    """Invalid response from IDA instance."""


class IDAIPCClient:
    """Client for communicating with IDA instances via local sockets."""

    CONNECT_TIMEOUT = 2.0  # seconds
    READ_TIMEOUT = 5.0  # seconds
    SOCKET_PREFIX = "ida_ipc_"

    @staticmethod
    def discover_instances() -> list[IDAInstance]:
        """Find all running IDA instances with IPC sockets.

        Returns:
            List of IDAInstance objects representing running IDA instances.
            Stale sockets (from crashed IDA instances) are automatically cleaned up.
        """
        if sys.platform == "win32":
            return IDAIPCClient._discover_windows()
        else:
            return IDAIPCClient._discover_unix()

    @staticmethod
    def _discover_unix() -> list[IDAInstance]:
        """Discover IDA instances on Unix-like systems."""
        instances = []
        socket_pattern = f"/tmp/{IDAIPCClient.SOCKET_PREFIX}*"

        for sock_path in glob.glob(socket_pattern):
            try:
                # Extract PID from socket name
                basename = os.path.basename(sock_path)
                pid_str = basename.replace(IDAIPCClient.SOCKET_PREFIX, "")
                pid = int(pid_str)

                # Check if process is alive
                if IDAIPCClient._is_process_alive(pid):
                    instances.append(IDAInstance(pid=pid, socket_path=sock_path))
                else:
                    # Stale socket from crashed process, clean up
                    logger.debug(f"Removing stale socket: {sock_path}")
                    try:
                        os.unlink(sock_path)
                    except OSError:
                        pass
            except (ValueError, OSError) as e:
                logger.debug(f"Error processing socket {sock_path}: {e}")

        return instances

    @staticmethod
    def _discover_windows() -> list[IDAInstance]:
        """Discover IDA instances on Windows via named pipes."""
        instances = []

        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            psapi = ctypes.windll.psapi  # type: ignore[attr-defined]

            # Get all running process IDs
            process_ids = (wintypes.DWORD * 4096)()
            bytes_returned = wintypes.DWORD()

            if psapi.EnumProcesses(
                ctypes.byref(process_ids),
                ctypes.sizeof(process_ids),
                ctypes.byref(bytes_returned),
            ):
                num_processes = bytes_returned.value // ctypes.sizeof(wintypes.DWORD)

                for i in range(num_processes):
                    pid = process_ids[i]
                    if pid == 0:
                        continue

                    # Try to connect to this PID's socket
                    pipe_name = f"\\\\.\\pipe\\{IDAIPCClient.SOCKET_PREFIX}{pid}"
                    try:
                        handle = kernel32.CreateFileW(
                            pipe_name,
                            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
                            0,
                            None,
                            3,  # OPEN_EXISTING
                            0,
                            None,
                        )
                        if handle != -1:
                            kernel32.CloseHandle(handle)
                            instances.append(IDAInstance(pid=pid, socket_path=pipe_name))
                    except OSError:
                        pass

        except OSError as e:
            logger.debug(f"Windows pipe enumeration failed: {e}")

        return instances

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check if a process with the given PID is running."""
        try:
            if sys.platform == "win32":
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                os.kill(pid, 0)
                return True
        except (OSError, PermissionError):
            return False

    @staticmethod
    def query_instance(socket_path: str) -> IDAInstance | None:
        """Query an IDA instance for its IDB info.

        Args:
            socket_path: Path to the socket file or named pipe.

        Returns:
            IDAInstance with IDB info populated, or None if query failed.
        """
        try:
            response = IDAIPCClient._send_command(socket_path, {"cmd": "get_info"})
            if response.get("status") == "ok":
                basename = os.path.basename(socket_path).replace("\\\\.\\pipe\\", "")
                pid_str = basename.replace(IDAIPCClient.SOCKET_PREFIX, "")
                pid = int(pid_str)

                return IDAInstance(
                    pid=pid,
                    socket_path=socket_path,
                    idb_path=response.get("idb_path"),
                    idb_name=response.get("idb_name"),
                    has_idb=response.get("has_idb", False),
                )
        except (OSError, IDAIPCError, ValueError) as e:
            logger.debug(f"Failed to query instance at {socket_path}: {e}")

        return None

    @staticmethod
    def ping(socket_path: str) -> bool:
        """Check if an IDA instance is responsive."""
        try:
            response = IDAIPCClient._send_command(socket_path, {"cmd": "ping"})
            return response.get("status") == "ok"
        except (OSError, IDAIPCError):
            return False

    @staticmethod
    def send_open_ida_link(socket_path: str, uri: str) -> tuple[bool, str]:
        """Send open_ida_link command to an IDA instance.

        Args:
            socket_path: Path to the socket file or named pipe.
            uri: The ida:// URI to open.

        Returns:
            Tuple of (success: bool, message: str).
        """
        try:
            response = IDAIPCClient._send_command(socket_path, {"cmd": "open_ida_link", "uri": uri})
            if response.get("status") == "ok":
                return True, "OK"
            else:
                return False, response.get("message", "Unknown error")
        except IDAIPCError as e:
            return False, str(e)
        except OSError as e:
            return False, f"Unexpected error: {e}"

    @staticmethod
    def is_analysis_complete(socket_path: str) -> AnalysisResult:
        """Check if IDA auto-analysis is complete (non-blocking).

        Args:
            socket_path: Path to the socket file or named pipe.

        Returns:
            AnalysisResult with success=True if analysis is complete,
            success=False if still in progress or error.
        """
        try:
            response = IDAIPCClient._send_command(socket_path, {"cmd": "is_analysis_complete"})
            if response.get("status") == "ok":
                return AnalysisResult(
                    success=response.get("analysis_complete", False),
                    status="ok",
                    message=None,
                )
            return AnalysisResult(
                success=False,
                status=response.get("status", "error"),
                message=response.get("message"),
            )
        except IDAIPCError as e:
            return AnalysisResult(
                success=False,
                status="error",
                message=str(e),
            )

    @staticmethod
    def _send_command(socket_path: str, command: dict, read_timeout: float | None = None) -> dict:
        """Send a JSON command and receive response."""
        if sys.platform == "win32":
            return IDAIPCClient._send_command_windows(socket_path, command, read_timeout=read_timeout)
        else:
            return IDAIPCClient._send_command_unix(socket_path, command, read_timeout=read_timeout)

    @staticmethod
    def _send_command_unix(socket_path: str, command: dict, read_timeout: float | None = None) -> dict:
        """Send command via Unix domain socket."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(IDAIPCClient.CONNECT_TIMEOUT)

        try:
            sock.connect(socket_path)
        except TimeoutError:
            raise IPCTimeoutError(f"Connection to {socket_path} timed out")
        except OSError as e:
            raise IPCConnectionError(f"Failed to connect to {socket_path}: {e}")

        try:
            data = json.dumps(command).encode("utf-8")
            sock.sendall(data)

            sock.settimeout(read_timeout or IDAIPCClient.READ_TIMEOUT)
            response_data = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response_data += chunk
                    try:
                        return json.loads(response_data.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                except TimeoutError:
                    break

            if not response_data:
                raise IPCProtocolError("No response received")

            try:
                return json.loads(response_data.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise IPCProtocolError(f"Invalid JSON response: {e}")

        finally:
            sock.close()

    @staticmethod
    def _send_command_windows(pipe_path: str, command: dict, read_timeout: float | None = None) -> dict:
        """Send command via Windows named pipe."""
        # Note: read_timeout is accepted for API consistency but Windows pipes
        # use blocking I/O by default
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

            handle = kernel32.CreateFileW(
                pipe_path,
                0x80000000 | 0x40000000,
                0,
                None,
                3,
                0,
                None,
            )

            if handle == -1:
                raise IPCConnectionError(f"Failed to open pipe: {pipe_path}")

            try:
                data = json.dumps(command).encode("utf-8")
                bytes_written = wintypes.DWORD()
                success = kernel32.WriteFile(handle, data, len(data), ctypes.byref(bytes_written), None)
                if not success:
                    raise IPCConnectionError("Failed to write to pipe")

                buffer = ctypes.create_string_buffer(4096)
                bytes_read = wintypes.DWORD()
                success = kernel32.ReadFile(handle, buffer, 4096, ctypes.byref(bytes_read), None)

                if success and bytes_read.value > 0:
                    response_data = buffer.raw[: bytes_read.value]
                    try:
                        return json.loads(response_data.decode("utf-8"))
                    except json.JSONDecodeError as e:
                        raise IPCProtocolError(f"Invalid JSON response: {e}")
                else:
                    raise IPCProtocolError("No response received")

            finally:
                kernel32.CloseHandle(handle)

        except ImportError:
            raise IPCConnectionError("Windows named pipe support not available")


def find_instance_for_idb(idb_name: str) -> IDAInstance | None:
    """Find an IDA instance that has the specified IDB open.

    Args:
        idb_name: The IDB name to search for (basename without extension).

    Returns:
        IDAInstance if found, None otherwise.
    """
    instances = IDAIPCClient.discover_instances()

    for instance in instances:
        info = IDAIPCClient.query_instance(instance.socket_path)
        if info and info.has_idb and info.idb_name and info.idb_name.lower() == idb_name.lower():
            return info

    return None


def find_all_instances_with_info() -> list[IDAInstance]:
    """Find all IDA instances and query their IDB info."""
    instances = IDAIPCClient.discover_instances()
    result = []

    for instance in instances:
        info = IDAIPCClient.query_instance(instance.socket_path)
        if info:
            result.append(info)
        else:
            result.append(instance)

    return result
