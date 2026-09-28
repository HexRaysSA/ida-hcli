"""get_hcli_command returns unquoted argv tokens (executable first), so callers can
render them with target-appropriate quoting instead of hand-rolled string escaping."""

from unittest.mock import patch

import pytest

from hcli.lib.util import io


@pytest.mark.parametrize(
    ("on_path", "expected"),
    [
        # hcli on PATH: one token, no embedded quotes, even for a spaced install path.
        ({"hcli": "/opt/My Tools/hcli"}, ["/opt/My Tools/hcli"]),
        # development environment: uv and its arguments stay distinct tokens.
        ({"uv": "/usr/bin/uv"}, ["/usr/bin/uv", "run", "hcli"]),
        # last resort: module invocation, under whichever python name resolves.
        ({"python3": "/usr/bin/python3"}, ["/usr/bin/python3", "-m", "hcli"]),
    ],
)
def test_resolution_order(on_path, expected):
    with patch("hcli.lib.util.io.shutil.which", on_path.get):
        assert io.get_hcli_command() == expected


def test_nothing_on_path_raises():
    with patch("hcli.lib.util.io.shutil.which", lambda name: None), pytest.raises(RuntimeError):
        io.get_hcli_command()


def test_frozen_returns_the_executable_as_one_token():
    with (
        patch.object(io.sys, "frozen", True, create=True),
        patch.object(io.sys, "executable", "/opt/My Tools/hcli"),
    ):
        assert io.get_hcli_command() == ["/opt/My Tools/hcli"]


def _fake_script(tmp_path, name="hcli"):
    script = tmp_path / "bin" / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("")
    return script


def test_running_script_wins_over_a_different_hcli_on_path(tmp_path):
    # e.g. a fresh venv's hcli while an old `pip install --user` hcli comes first on PATH.
    script = _fake_script(tmp_path)
    with (
        patch.object(io.sys, "argv", [str(script)]),
        patch("hcli.lib.util.io.shutil.which", {"hcli": "/old/bin/hcli"}.get),
    ):
        assert io.get_hcli_command() == [str(script)]


def test_uvx_environment_goes_back_through_uvx(tmp_path):
    script = _fake_script(tmp_path / "archive-v0" / "abc123")
    with (
        patch.object(io.sys, "prefix", str(tmp_path / "archive-v0" / "abc123")),
        patch.object(io.sys, "argv", [str(script)]),
        patch("hcli.lib.util.io.shutil.which", {"uvx": "/usr/bin/uvx", "hcli": "/old/bin/hcli"}.get),
    ):
        assert io.get_hcli_command() == ["/usr/bin/uvx", "ida-hcli"]


class TestDisplayCommand:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        io.get_hcli_display_command.cache_clear()
        yield
        io.get_hcli_display_command.cache_clear()

    def test_uvx(self, tmp_path):
        # The reported case: `uvx ida-hcli mcp install` must not suggest a bare `hcli`,
        # which may be missing or an older install.
        with (
            patch.object(io.sys, "prefix", str(tmp_path / ".cache" / "uv" / "archive-v0" / "abc123")),
            patch("hcli.lib.util.io.shutil.which", {"hcli": "/old/bin/hcli"}.get),
        ):
            assert io.get_hcli_display_command() == "uvx ida-hcli"

    def test_uvx_cached_environment_bucket(self, tmp_path):
        with patch.object(io.sys, "prefix", str(tmp_path / "uv" / "environments-v2" / "ida-hcli-abc")):
            assert io.get_hcli_display_command() == "uvx ida-hcli"

    def test_uv_tool_install_is_not_uvx(self, tmp_path):
        assert not _is_uvx(tmp_path / ".local" / "share" / "uv" / "tools" / "ida-hcli")

    def test_short_name_when_path_resolves_to_this_script(self, tmp_path):
        script = _fake_script(tmp_path)
        with (
            patch.object(io.sys, "argv", [str(script)]),
            patch("hcli.lib.util.io.shutil.which", {"hcli": str(script)}.get),
        ):
            assert io.get_hcli_display_command() == "hcli"

    def test_invoked_name_is_kept(self, tmp_path):
        script = _fake_script(tmp_path, "ida-hcli")
        with (
            patch.object(io.sys, "argv", [str(script)]),
            patch("hcli.lib.util.io.shutil.which", {"ida-hcli": str(script)}.get),
        ):
            assert io.get_hcli_display_command() == "ida-hcli"

    def test_full_path_when_path_has_another_hcli(self, tmp_path):
        script = _fake_script(tmp_path / "my tools")
        with (
            patch.object(io.sys, "argv", [str(script)]),
            patch.object(io.sys, "platform", "linux"),
            patch("hcli.lib.util.io.shutil.which", {"hcli": "/old/bin/hcli"}.get),
        ):
            assert io.get_hcli_display_command() == f"'{script}'"

    def test_python_dash_m(self):
        with (
            patch.object(io.sys, "argv", ["/src/hcli/__main__.py"]),
            patch.object(io.sys, "executable", "/usr/bin/python3"),
            patch.object(io.sys, "platform", "linux"),
        ):
            assert io.get_hcli_display_command() == "/usr/bin/python3 -m hcli"

    def test_unknown_host_program_falls_back_to_binary_name(self):
        with patch.object(io.sys, "argv", ["/usr/bin/pytest"]):
            assert io.get_hcli_display_command() == "hcli"


def _is_uvx(prefix) -> bool:
    with patch.object(io.sys, "prefix", str(prefix)):
        return io.is_uvx_environment()
