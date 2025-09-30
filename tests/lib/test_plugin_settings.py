import logging
import os
import subprocess
from pathlib import Path

import pytest
from fixtures import *  # noqa
from fixtures import (
    PLUGINS_DIR,
    install_this_package_in_venv,
    run_hcli,
    temp_env_var,
)

logger = logging.getLogger(__name__)


def test_plugin_settings_integration(virtual_ida_environment_with_venv):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    with temp_env_var("TERM", "dumb"):
        with temp_env_var("COLUMNS", "80"):
            p = run_hcli("plugin config --help")
            assert "Usage:" in p.stdout
            assert "python -m hcli.main plugin config" in p.stdout
            assert "[OPTIONS] PLUGIN_NAME COMMAND [ARGS]" in p.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} install plugin1==4.0.0")
            assert "Installed plugin: plugin1==4.0.0\n" == p.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 list")
            assert "No settings defined for plugin1\n" == p.stdout

            with pytest.raises(subprocess.CalledProcessError) as e:
                _ = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 set foo bar")
            assert "Error: 'unknown setting: foo'\n" == e.value.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 list")
            assert "No settings defined for plugin1\n" == p.stdout

            _ = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} uninstall plugin1")

            with pytest.raises(subprocess.CalledProcessError) as e:
                _ = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} install plugin1==5.0.0")
            assert (
                e.value.stdout
                == "Error: plugin requires configuration but console is not interactive. Please \nprovide settings via command line: --config key1=<value>\n"
            )

            with pytest.raises(subprocess.CalledProcessError) as e:
                _ = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} install plugin1==5.0.0 --config foo=bar")
            assert "Error: 'unknown setting: foo'\n" == e.value.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} install plugin1==5.0.0 --config key1=bar")
            assert "Installed plugin: plugin1==5.0.0\n" == p.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 list")
            assert "Key   Value                Description" in p.stdout
            assert "key1  bar                  the value for key 1" in p.stdout
            assert "key2  default 2 (default)  the value for key 2" in p.stdout

            with pytest.raises(subprocess.CalledProcessError) as e:
                _ = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 set key2 baz")
            assert "Error: failed to validate setting value: plugin1: key2: 'baz'" in e.value.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 set key2 'default 3'")
            assert "Set plugin1.key2\n" == p.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 list")
            assert "Key   Value      Description" in p.stdout
            assert "key1  bar        the value for key 1" in p.stdout
            assert "key2  default 3  the value for key 2" in p.stdout

            with pytest.raises(subprocess.CalledProcessError) as e:
                p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 del key1")
            assert "Error: cannot delete required setting without default: plugin1: key1\n" == e.value.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 del key2")
            assert "Deleted plugin1.key2\n" == p.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} config plugin1 list")
            assert "Key   Value                Description" in p.stdout
            assert "key1  bar                  the value for key 1" in p.stdout
            assert "key2  default 2 (default)  the value for key 2" in p.stdout
