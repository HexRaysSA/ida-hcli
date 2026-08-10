import pytest

import hcli.lib.ida.python


@pytest.fixture(autouse=True)
def _clear_python_probe_cache():
    """The idat probe is cached per process, but tests change the environment
    (fake IDAUSR directories, env vars) and expect a fresh probe each time.
    """
    hcli.lib.ida.python.probe_current_python_info.cache_clear()
    yield
