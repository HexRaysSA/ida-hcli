import pytest
from fixtures import *

from hcli.lib.ida.plugin.context import IDAEnvironment, InstallContext, InstallOptions
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions, PythonNotFoundError
from hcli.lib.venv import PythonVersion


def test_install_options_defaults():
    opts = InstallOptions()
    assert opts.pip_options == PIP_OPTIONS_DEFAULT
    assert opts.check_environment is True


def test_install_options_custom():
    pip = PipOptions(offline=True)
    opts = InstallOptions(pip_options=pip, check_environment=False)
    assert opts.pip_options.offline is True
    assert opts.check_environment is False


def test_install_context_defaults():
    env = IDAEnvironment(platform="linux-x86_64", ida_version="9.0")
    ctx = InstallContext(env=env)
    assert ctx.env is env
    assert ctx.options.check_environment is True
    assert ctx.options.pip_options == PIP_OPTIONS_DEFAULT


def test_ida_environment_probes_python_version_lazily_and_once(monkeypatch):
    calls = []

    def fake_detect():
        calls.append(1)
        return PythonVersion(3, 11, 9)

    monkeypatch.setattr("hcli.lib.ida.plugin.context.detect_current_python_version", fake_detect)
    env = IDAEnvironment(platform="linux-x86_64", ida_version="9.0")
    assert calls == []
    assert env.python_version == "3.11.9"
    assert env.python_version == "3.11.9"
    assert len(calls) == 1


def test_ida_environment_remembers_a_failed_python_probe(virtual_ida_environment_without_python):
    env = IDAEnvironment(platform="linux-x86_64", ida_version="9.0")

    with pytest.raises(PythonNotFoundError) as first:
        _ = env.python_version
    with pytest.raises(PythonNotFoundError) as second:
        _ = env.python_version

    assert second.value is first.value
