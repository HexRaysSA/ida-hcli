"""HCLI_CONFIG_NAMESPACE controls the namespace of the stored auth keys.

It defaults to HCLI_BINARY_NAME so each binary keeps its own login, but can be set
explicitly so sibling binaries share a single credential store while still
differing on their binary identity.
"""

import importlib
import os
from unittest.mock import patch

import pytest

import hcli.env
import hcli.lib.constants.auth as auth_const


@pytest.fixture(autouse=True)
def _restore_reloaded_modules():
    # Both modules read the environment at import time, so tests reload them under
    # patched env. Reload once more afterwards to restore the pristine state.
    yield
    importlib.reload(hcli.env)
    importlib.reload(auth_const)


def _reload_with(binary=None, namespace=None):
    """Reload env + auth constants with a clean, explicit set of env vars."""
    with patch.dict(os.environ, {}, clear=False):
        for key in ("HCLI_BINARY_NAME", "HCLI_CONFIG_NAMESPACE"):
            os.environ.pop(key, None)
        if binary is not None:
            os.environ["HCLI_BINARY_NAME"] = binary
        if namespace is not None:
            os.environ["HCLI_CONFIG_NAMESPACE"] = namespace
        importlib.reload(hcli.env)
        importlib.reload(auth_const)
        return hcli.env.ENV, auth_const


@pytest.mark.parametrize(
    ("binary", "namespace", "expected_binary", "expected_credentials"),
    [
        (None, None, "hcli", "hcli.credentials"),
        ("other", None, "other", "other.credentials"),
        # An explicit namespace decouples the auth keys from the binary identity.
        ("other", "hcli", "other", "hcli.credentials"),
    ],
)
def test_auth_keys_follow_the_namespace(binary, namespace, expected_binary, expected_credentials):
    env, const = _reload_with(binary=binary, namespace=namespace)
    assert env.HCLI_BINARY_NAME == expected_binary
    assert const.CONFIG_CREDENTIALS == expected_credentials
