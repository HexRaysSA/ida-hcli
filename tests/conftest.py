import contextlib
import sys
from collections.abc import Iterator

import pytest
from click.testing import CliRunner

import hcli.lib.ida.python
from hcli.lib.console import console, stderr_console


@pytest.fixture(autouse=True)
def _clear_python_probe_cache():
    """The idat probe is cached per process, but tests change the environment
    (fake IDAUSR directories, env vars) and expect a fresh probe each time.
    """
    hcli.lib.ida.python.probe_current_python_info.cache_clear()
    yield


@pytest.fixture(autouse=True)
def _pin_console_streams_during_cli_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point hcli's global Rich consoles at CliRunner's streams while it is active.

    In production the consoles are unpinned, so Rich resolves sys.stdout/stderr
    on every write. That breaks under CliRunner: pytest's live-logging handler
    replaces sys.stdout mid-invoke, so Rich output lands in pytest's capture
    stream instead of CliRunner's, and CliRunner's now-unreferenced buffer is
    garbage collected and closed (see #190). Pinning for the duration of the
    isolation block both routes output correctly and keeps the buffer alive.
    """
    isolation = CliRunner.isolation

    @contextlib.contextmanager
    def pinned_isolation(self: CliRunner, *args, **kwargs) -> Iterator:
        with isolation(self, *args, **kwargs) as streams:
            console.file = sys.stdout
            stderr_console.file = sys.stderr
            try:
                yield streams
            finally:
                console._file = None
                stderr_console._file = None

    monkeypatch.setattr(CliRunner, "isolation", pinned_isolation)
