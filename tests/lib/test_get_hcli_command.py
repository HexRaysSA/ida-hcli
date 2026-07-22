"""get_hcli_command returns unquoted argv tokens (executable first), so callers can
render them with target-appropriate quoting instead of hand-rolled string escaping."""

import shlex
from unittest.mock import patch

from hcli.lib.util import io


def test_path_install_is_a_single_unquoted_token():
    # hcli on PATH, install path contains spaces -> one token, no embedded quotes.
    with patch(
        "hcli.lib.util.io.shutil.which",
        lambda name: "/opt/My Tools/hcli" if name == "hcli" else None,
    ):
        assert io.get_hcli_command() == ["/opt/My Tools/hcli"]


def test_uv_fallback_carries_its_args_as_separate_tokens():
    # hcli not on PATH, uv available -> the binary and its args are distinct tokens.
    with patch(
        "hcli.lib.util.io.shutil.which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    ):
        assert io.get_hcli_command() == ["/usr/bin/uv", "run", "hcli"]


def test_python_fallback_uses_module_invocation():
    # Neither hcli nor uv on PATH -> fall back to `python -m hcli`.
    with patch(
        "hcli.lib.util.io.shutil.which",
        lambda name: "/usr/bin/python3" if name in ("python", "python3") else None,
    ):
        assert io.get_hcli_command() == ["/usr/bin/python3", "-m", "hcli"]


def test_frozen_returns_the_executable_as_one_token():
    with (
        patch.object(io.sys, "frozen", True, create=True),
        patch.object(io.sys, "executable", "/opt/My Tools/hcli"),
    ):
        assert io.get_hcli_command() == ["/opt/My Tools/hcli"]


def test_tokens_render_space_safe_through_shlex():
    # The contract: a spaced install path survives shlex.join as a single quoted word,
    # so an interpolating shell/handler can't word-split it.
    with patch(
        "hcli.lib.util.io.shutil.which",
        lambda name: "/opt/My Tools/hcli" if name == "hcli" else None,
    ):
        assert shlex.join(io.get_hcli_command()) == "'/opt/My Tools/hcli'"
