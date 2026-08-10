import pytest

import hcli.lib.ida.python


@pytest.fixture(autouse=True)
def _clear_python_probe_cache(tmp_path, monkeypatch):
    """Isolate both layers of the Python probe cache between tests."""
    monkeypatch.setenv("HCLI_CACHE_DIR", str(tmp_path / "hcli-cache"))
    hcli.lib.ida.python.probe_current_python_info.cache_clear()
    yield
