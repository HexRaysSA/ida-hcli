from hcli.lib.ida.plugin.context import IDAEnvironment, InstallContext, InstallOptions
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions


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
