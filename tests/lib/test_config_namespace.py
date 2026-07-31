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


def test_namespace_defaults_to_default_binary_name():
    env, const = _reload_with()
    assert env.HCLI_CONFIG_NAMESPACE == "hcli"
    assert const.CONFIG_CREDENTIALS == "hcli.credentials"
    assert const.CONFIG_LOGIN_EMAIL == "hcli.login.email"


def test_namespace_follows_custom_binary_name_by_default():
    env, const = _reload_with(binary="other")
    assert env.HCLI_BINARY_NAME == "other"
    assert env.HCLI_CONFIG_NAMESPACE == "other"
    assert const.CONFIG_CREDENTIALS == "other.credentials"
    assert const.CONFIG_LOGIN_EMAIL == "other.login.email"


def test_explicit_namespace_decouples_auth_keys_from_binary_name():
    env, const = _reload_with(binary="other", namespace="hcli")
    # Binary identity stays distinct...
    assert env.HCLI_BINARY_NAME == "other"
    # ...while the auth keys resolve to the shared namespace.
    assert env.HCLI_CONFIG_NAMESPACE == "hcli"
    assert const.CONFIG_CREDENTIALS == "hcli.credentials"
    assert const.CONFIG_LOGIN_EMAIL == "hcli.login.email"
