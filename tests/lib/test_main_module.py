"""`python -m hcli` must launch the CLI (regression guard for the missing __main__)."""

import subprocess
import sys


def test_python_m_hcli_runs_cli():
    result = subprocess.run(
        [sys.executable, "-m", "hcli", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "version" in (result.stdout + result.stderr).lower()
