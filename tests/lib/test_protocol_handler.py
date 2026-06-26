"""Tests for the ida:// protocol handler registration hardening."""

from __future__ import annotations

from click.testing import CliRunner

from hcli.commands.ida.open import open_ida_link


class TestOpenArgSeparator:
    """The Windows registry command uses `ida open -- "%1"`. These tests confirm the
    `--` separator turns any injected tokens into surplus positionals that click
    rejects, rather than options it would interpret."""

    def test_url_after_separator_is_the_uri(self):
        # A normal URL after `--` is accepted as the single positional argument.
        # --no-launch keeps the test from touching IDA/instances.
        runner = CliRunner()
        result = runner.invoke(open_ida_link, ["--no-launch", "--", "ida://ke/x.i64/functions?url=http://h/a"])
        # It parsed fine (no usage error). Behaviour beyond parsing is out of scope here.
        assert "no such option" not in result.output.lower()
        assert "Got unexpected extra argument" not in result.output

    def test_injected_option_after_separator_is_rejected(self):
        # Simulates %1 = `x" --no-launch "` → `ida open -- "x" --no-launch ""`.
        # After `--`, the extra tokens are positionals; open takes only one → error.
        runner = CliRunner()
        result = runner.invoke(open_ida_link, ["--", "ida://ke/x.i64/f", "--no-launch", ""])
        assert result.exit_code != 0
        assert "Got unexpected extra argument" in result.output
