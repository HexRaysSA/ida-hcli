import pytest
import rich.console
import rich.errors

import hcli.lib.ida.python


@pytest.fixture(autouse=True)
def _clear_python_probe_cache():
    """The idat probe is cached per process, but tests change the environment
    (fake IDAUSR directories, env vars) and expect a fresh probe each time.
    """
    hcli.lib.ida.python.probe_current_python_info.cache_clear()
    yield


@pytest.fixture(autouse=True)
def _fail_on_nested_live_display(monkeypatch):
    """Fail loudly if code opens a rich Status/Progress/Live while another
    one is already active on the same console.

    Recent rich versions silently no-op the inner display instead of raising
    (older/vendored versions raise ``LiveError: Only one live display may be
    active at once``), so this bug can hide behind a passing test suite and
    only surface for users on a different rich version. `Console.set_live` is
    where both behaviors branch, so patch it to always raise on nesting.
    """
    original_set_live = rich.console.Console.set_live

    def strict_set_live(self, live):
        if self._live_stack:
            raise rich.errors.LiveError(
                "Only one live display may be active at once "
                "(nested rich Status/Progress/Live on the same console)"
            )
        return original_set_live(self, live)

    monkeypatch.setattr(rich.console.Console, "set_live", strict_set_live)
