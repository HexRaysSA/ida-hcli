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
