"""Tests for the ida:// protocol handler registration hardening."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from hcli.commands.ida.open import open_ida_link


class TestOpenArgSeparator:
    """The Windows registry command uses `ida open -- "%1"`. These tests confirm the
    `--` separator turns any injected tokens into surplus positionals that click
    rejects, rather than options it would interpret. The handler dispatch is mocked
    so only argument parsing is exercised — no real download/IPC/dialog I/O runs."""

    def test_url_after_separator_is_dispatched_as_the_uri(self):
        # A normal URL after `--` is parsed as the single positional and dispatched
        # verbatim to the handler. A fake handler captures it so no real I/O happens.
        captured: dict[str, str] = {}
        fake = MagicMock()
        fake.matches.return_value = True
        fake.handle.side_effect = lambda uri, *a, **k: captured.__setitem__("uri", uri)

        runner = CliRunner()
        with patch("hcli.commands.ida.open.HANDLERS", [fake]):
            result = runner.invoke(open_ida_link, ["--", "ida://ke/x.i64/functions?url=http://h/a"])

        assert result.exit_code == 0, result.output
        assert captured.get("uri") == "ida://ke/x.i64/functions?url=http://h/a"

    def test_injected_option_after_separator_is_rejected(self):
        # Simulates %1 = `x" --no-launch "` → `ida open -- "x" --no-launch ""`.
        # After `--`, the extra tokens are positionals; open takes only one → error.
        runner = CliRunner()
        result = runner.invoke(open_ida_link, ["--", "ida://ke/x.i64/f", "--no-launch", ""])
        assert result.exit_code != 0
        assert "Got unexpected extra argument" in result.output
