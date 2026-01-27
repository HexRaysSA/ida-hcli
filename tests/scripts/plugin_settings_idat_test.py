#!/usr/bin/env python3
"""Integration test for plugin settings API path handling.

This script tests that:
1. Plugins can call get_current_plugin() from within IDA
2. Plugins can call get_current_plugin_setting() to read settings
3. Path handling works correctly across platforms (especially Windows)

Usage:
    python test_plugin_settings.py <idat_path> <test_binary>

The script will:
1. Find IDA's Python interpreter
2. pip install hcli into IDA's Python
3. Create a temporary IDAUSR directory
4. Install the settings-test plugin
5. Configure plugin settings
6. Run idat to load the plugin
7. Verify the plugin ran correctly

Exit codes:
    0: Success
    1: Test failed
    2: Setup error
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent.parent
PLUGIN_ZIP = REPO_ROOT / "tests" / "data" / "plugins" / "settings-test" / "settings-test-v1.0.0.zip"
DUMMY_SCRIPT = SCRIPT_DIR / "hi.py"

FIND_PYTHON_SCRIPT = """
import shutil
import os.path
import sys
import json
def find_python_executable():
    exe = sys.executable
    if "python" in os.path.basename(exe).lower():
      return exe
    return shutil.which("python3") or shutil.which("python")
print("__hcli__:" + json.dumps(find_python_executable()))
sys.exit()
"""


def find_ida_python(idat_path: str, tmpdir: Path) -> Path | None:
    """Find IDA's Python interpreter by running idat with a discovery script."""
    script_path = tmpdir / "find_python.py"
    log_path = tmpdir / "find_python.log"

    script_path.write_text(FIND_PYTHON_SCRIPT)

    cmd = [
        idat_path,
        "-a",
        "-A",
        "-c",
        "-t",
        f"-L{log_path}",
        f"-S{script_path}",
    ]

    print(f"Finding IDA Python: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if not log_path.exists():
        print("Warning: Log file not created, idat may have failed")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        return None

    log_content = log_path.read_text(errors="replace")
    for line in log_content.splitlines():
        if line.startswith("__hcli__:"):
            python_path = json.loads(line[len("__hcli__:") :])
            return Path(python_path)

    print("Warning: Could not find Python path in IDA log")
    print(f"Log content: {log_content}")
    return None


def install_hcli_to_ida_python(ida_python: Path):
    """Install hcli into IDA's Python environment."""
    print(f"Installing hcli into IDA Python: {ida_python}")

    cmd = [str(ida_python), "-m", "pip", "install", str(REPO_ROOT)]
    print(f"Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("pip install failed:")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        raise RuntimeError("Failed to install hcli into IDA Python")

    print("hcli installed successfully")


def setup_idausr(tmpdir: Path) -> Path:
    """Create IDAUSR directory structure."""
    idausr = tmpdir / "idausr"
    plugins_dir = idausr / "plugins"
    plugins_dir.mkdir(parents=True)
    return idausr


def install_plugin(idausr: Path) -> Path:
    """Install the settings-test plugin to IDAUSR."""
    import zipfile

    plugins_dir = idausr / "plugins"
    plugin_dir = plugins_dir / "settings-test"

    with zipfile.ZipFile(PLUGIN_ZIP, "r") as zf:
        for member in zf.namelist():
            if member.startswith("src/"):
                relative_path = member[4:]
                if relative_path:
                    target = plugin_dir / relative_path
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            dst.write(src.read())

    return plugin_dir


def configure_settings(idausr: Path):
    """Configure plugin settings in ida.json."""
    config_path = idausr / "ida.json"
    config = {
        "plugins": {
            "settings-test": {
                "settings": {
                    "test_value": "configured_value",
                    "test_bool": True,
                }
            }
        }
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def run_idat(idat_path: str, test_binary: str, idausr: Path, output_file: Path, log_file: Path) -> int:
    """Run idat with the test environment."""
    env = os.environ.copy()
    env["IDAUSR"] = str(idausr)
    env["SETTINGS_TEST_OUTPUT"] = str(output_file)

    cmd = [
        idat_path,
        "-a",
        "-A",
        "-c",
        "-t",
        f"-L{log_file}",
        f"-S{DUMMY_SCRIPT}",
        test_binary,
    ]

    print(f"Running: {' '.join(cmd)}")
    print(f"IDAUSR={idausr}")
    print(f"SETTINGS_TEST_OUTPUT={output_file}")

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    print(f"idat exit code: {result.returncode}")
    if result.stdout:
        print(f"stdout: {result.stdout}")
    if result.stderr:
        print(f"stderr: {result.stderr}")

    return result.returncode


def verify_results(output_file: Path) -> bool:
    """Verify the plugin produced correct results."""
    if not output_file.exists():
        print(f"ERROR: Output file does not exist: {output_file}")
        return False

    with open(output_file, "r", encoding="utf-8") as f:
        results = json.load(f)

    print(f"Plugin results: {json.dumps(results, indent=2)}")

    if not results.get("success"):
        print(f"ERROR: Plugin reported failure: {results.get('errors')}")
        return False

    if results.get("plugin_name") != "settings-test":
        print(f"ERROR: Wrong plugin name: {results.get('plugin_name')}")
        return False

    if results.get("test_value") != "configured_value":
        print(f"ERROR: Wrong test_value: {results.get('test_value')}")
        return False

    if results.get("test_bool") is not True:
        print(f"ERROR: Wrong test_bool: {results.get('test_bool')}")
        return False

    return True


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <idat_path> <test_binary>")
        sys.exit(2)

    idat_path = sys.argv[1]
    test_binary = sys.argv[2]

    if not Path(idat_path).exists():
        print(f"ERROR: idat not found: {idat_path}")
        sys.exit(2)

    if not Path(test_binary).exists():
        print(f"ERROR: Test binary not found: {test_binary}")
        sys.exit(2)

    if not PLUGIN_ZIP.exists():
        print(f"ERROR: Plugin ZIP not found: {PLUGIN_ZIP}")
        sys.exit(2)

    print(f"Platform: {platform.system()}")
    print(f"idat: {idat_path}")
    print(f"Test binary: {test_binary}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        print("\n=== Finding IDA Python ===")
        ida_python = find_ida_python(idat_path, tmpdir)
        if ida_python:
            print(f"Found IDA Python: {ida_python}")

            print("\n=== Installing hcli ===")
            try:
                install_hcli_to_ida_python(ida_python)
            except RuntimeError as e:
                print(f"Warning: {e}")
                print("Continuing without hcli installed - test may fail")
        else:
            print("Warning: Could not find IDA Python, test may fail")

        print("\n=== Setting up IDAUSR ===")
        idausr = setup_idausr(tmpdir)
        print(f"IDAUSR: {idausr}")

        print("\n=== Installing plugin ===")
        plugin_dir = install_plugin(idausr)
        print(f"Plugin installed to: {plugin_dir}")

        for p in plugin_dir.rglob("*"):
            print(f"  {p.relative_to(plugin_dir)}")

        print("\n=== Configuring settings ===")
        configure_settings(idausr)

        output_file = tmpdir / "settings_test_results.json"
        log_file = tmpdir / "ida.log"

        print("\n=== Running idat ===")
        run_idat(idat_path, test_binary, idausr, output_file, log_file)

        if log_file.exists():
            print("\n=== IDA Log ===")
            print(log_file.read_text(encoding="utf-8", errors="replace"))

        print("\n=== Verifying results ===")
        if verify_results(output_file):
            print("\nSUCCESS: Plugin settings test passed!")
            sys.exit(0)
        else:
            print("\nFAILED: Plugin settings test failed!")
            sys.exit(1)


if __name__ == "__main__":
    main()
