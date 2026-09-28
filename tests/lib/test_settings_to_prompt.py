from hcli.lib.ida.plugin import PluginSettingDescriptor
from hcli.lib.ida.plugin.settings import get_settings_to_prompt

API_KEY = PluginSettingDescriptor(key="api_key", type="string", required=True, name="API key", secret=True)
SERVER = PluginSettingDescriptor(key="server", type="string", required=False, default="eu.example.com", name="Server")
VERBOSE = PluginSettingDescriptor(key="verbose", type="boolean", required=False, default=False, name="Verbose")
TIMEOUT = PluginSettingDescriptor(
    key="timeout", type="string", required=False, default="60", name="Timeout", prompt=False
)
SETTINGS = [API_KEY, SERVER, VERBOSE, TIMEOUT]


def get_keys(settings: list[PluginSettingDescriptor]) -> list[str]:
    return [s.key for s in settings]


def test_first_install_prompts_every_promptable_setting():
    assert get_keys(get_settings_to_prompt(SETTINGS, {})) == ["api_key", "server", "verbose"]


def test_reinstall_skips_settings_with_stored_values():
    existing: dict[str, str | bool] = {"api_key": "secret", "verbose": True}
    assert get_keys(get_settings_to_prompt(SETTINGS, existing)) == ["server"]


def test_stored_value_equal_to_default_still_counts_as_configured():
    existing: dict[str, str | bool] = {"api_key": "secret", "server": "eu.example.com", "verbose": False}
    assert get_settings_to_prompt(SETTINGS, existing) == []


def test_stored_values_for_undeclared_keys_are_ignored():
    existing: dict[str, str | bool] = {"speakeasy_command": "speakeasy"}
    assert get_keys(get_settings_to_prompt(SETTINGS, existing)) == ["api_key", "server", "verbose"]
