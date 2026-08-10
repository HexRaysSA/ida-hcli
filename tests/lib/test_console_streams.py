"""hcli's output must survive `sys.stdout` being replaced while a command runs.

pytest's live-logging handler does exactly that under `--log-cli-level=DEBUG`,
which CI passes and `just test` does not. Rich resolves an unpinned console's
stream on every write, so hcli's output went to the replacement stream, and
CliRunner's buffer -- by then referenced only by the `sys.stdout` that was just
overwritten -- got garbage collected and closed underneath the command (#190).

tests/conftest.py pins the consoles for the duration of each isolation block.
Without that, this test fails the same way CI did, so it stands in for a flag
nobody remembers to pass locally.
"""

import gc
import io
import sys

import rich_click as click
from click.testing import CliRunner

from hcli.lib.console import console, stderr_console


@click.command()
def noisy() -> None:
    sys.stdout = io.StringIO()
    gc.collect()

    console.print("stdout marker")
    stderr_console.print("stderr marker")


def test_output_survives_stdout_replacement():
    original = sys.stdout
    try:
        result = CliRunner().invoke(noisy, catch_exceptions=False)
    finally:
        sys.stdout = original

    assert result.exit_code == 0
    # click 8.1 mixes stderr into output
    assert "stdout marker" in result.output
    assert "stderr marker" in result.output
