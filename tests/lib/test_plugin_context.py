from pathlib import Path

from hcli.lib.ida.plugin.context import IDAEnvironment, InstallContext, InstallOptions
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions


def test_ida_environment_frozen():
    env = IDAEnvironment(platform="linux-x86_64", ida_version="9.1")
    assert env.platform == "linux-x86_64"
    assert env.ida_version == "9.1"
    assert env.python_exe is None
    assert env.python_version is None


def test_ida_environment_with_python():
    env = IDAEnvironment(
        platform="macos-aarch64",
        ida_version="9.2",
        python_exe=Path("/usr/bin/python3"),
        python_version="3.12",
    )
    assert env.python_exe == Path("/usr/bin/python3")
    assert env.python_version == "3.12"


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


def test_install_context_full():
    env = IDAEnvironment(platform="windows-x86_64", ida_version="9.1")
    opts = InstallOptions(check_environment=False)
    ctx = InstallContext(env=env, options=opts)
    assert ctx.env.platform == "windows-x86_64"
    assert ctx.options.check_environment is False
