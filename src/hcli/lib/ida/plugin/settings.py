import inspect

from hcli.lib.ida import PluginConfig, get_ida_config, set_ida_config
from hcli.lib.ida.plugin import ChoiceValueError, PluginSettingDescriptor
from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, get_plugin_directory


def parse_setting_value(descriptor: PluginSettingDescriptor, string_value: str) -> str | bool:
    """Parse a string value to the appropriate type based on the setting descriptor.

    Args:
        descriptor: the setting descriptor
        string_value: the string value to parse

    Returns:
        The parsed value (str or bool)
    """
    if descriptor.type == "boolean":
        if string_value.lower() == "true":
            return True
        elif string_value.lower() == "false":
            return False
        else:
            raise ValueError(f"mismatching settings types: expected boolean ('true' or 'false'), got '{string_value}'")
    elif descriptor.type == "string":
        return string_value
    else:
        raise ValueError(f"unsupported setting type: {descriptor.type}")


def set_plugin_setting(plugin_name: str, key: str, value: str | bool):
    plugin_path = get_plugin_directory(plugin_name)
    metadata = get_metadata_from_plugin_directory(plugin_path)
    descr = metadata.plugin.get_setting(key)

    if descr.type == "string" and not isinstance(value, str):
        raise ValueError(f"mismatching settings types: {plugin_name}: {key}: {descr.type} vs {type(value).__name__}")
    elif descr.type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"mismatching settings types: {plugin_name}: {key}: {descr.type} vs {type(value).__name__}")

    try:
        descr.validate_value(value)
    except ChoiceValueError as e:
        choices_str = ", ".join(e.choices)
        raise ValueError(
            f"failed to validate setting value: {plugin_name}: {key}: '{value}' (must be one of: {choices_str})"
        ) from e
    except ValueError as e:
        raise ValueError(f"failed to validate setting value: {plugin_name}: {key}: '{value}'") from e

    config = get_ida_config()
    if plugin_name not in config.plugins:
        plugin_config = PluginConfig()
    else:
        plugin_config = config.plugins[plugin_name]

    if plugin_config.settings.get(key) == value:
        return

    plugin_config.settings[key] = value
    config.plugins[plugin_name] = plugin_config

    set_ida_config(config)


def get_plugin_setting(plugin_name: str, key: str) -> str | bool:
    plugin_path = get_plugin_directory(plugin_name)
    metadata = get_metadata_from_plugin_directory(plugin_path)
    descr = metadata.plugin.get_setting(key)

    config = get_ida_config()
    if plugin_name not in config.plugins:
        if descr.default is not None:
            return descr.default
        else:
            raise KeyError(f"plugin setting not found: {plugin_name}: {key}")

    plugin_config = config.plugins[plugin_name]
    if key not in plugin_config.settings:
        if descr.default is not None:
            return descr.default
        else:
            raise KeyError(f"plugin setting not found: {plugin_name}: {key}")

    value = plugin_config.settings[key]
    try:
        descr.validate_value(value)
    except ChoiceValueError as e:
        choices_str = ", ".join(e.choices)
        raise ValueError(
            f"failed to validate existing setting value: {plugin_name}: {key}: '{value}' (must be one of: {choices_str})"
        ) from e
    except ValueError as e:
        raise ValueError(f"failed to validate existing setting value: {plugin_name}: {key}: '{value}'") from e

    return value


def del_plugin_setting(plugin_name: str, key: str):
    plugin_path = get_plugin_directory(plugin_name)
    metadata = get_metadata_from_plugin_directory(plugin_path)
    descr = metadata.plugin.get_setting(key)

    if descr.required and not descr.default:
        raise ValueError(f"cannot delete required setting without default: {plugin_name}: {key}")

    config = get_ida_config()
    if plugin_name not in config.plugins:
        raise KeyError(f"plugin setting not found: {plugin_name}: {key}")

    plugin_config = config.plugins[plugin_name]
    if key not in plugin_config.settings:
        raise KeyError(f"plugin setting not found: {plugin_name}: {key}")

    del plugin_config.settings[key]
    config.plugins[plugin_name] = plugin_config

    set_ida_config(config)


def has_plugin_setting(plugin_name: str, key: str) -> bool:
    """Check if a plugin setting is explicitly set.

    Args:
        plugin_name: the plugin name
        key: the setting key

    Returns: True if the setting is explicitly set, False otherwise
    """
    plugin_path = get_plugin_directory(plugin_name)
    metadata = get_metadata_from_plugin_directory(plugin_path)
    metadata.plugin.get_setting(key)

    config = get_ida_config()
    if plugin_name not in config.plugins:
        return False

    plugin_config = config.plugins[plugin_name]
    return key in plugin_config.settings


def get_current_plugin() -> str:
    """Get the plugin name by walking the call stack.

    This must only be called from IDA Pro plugins, or it will raise RuntimeError.

    Returns:
        The plugin name extracted from the first plugin module found in the call stack.
    """
    frame = inspect.currentframe()
    if frame is None:
        raise RuntimeError("failed to get current frame")

    current_frame = frame.f_back
    while current_frame is not None:
        module_name = current_frame.f_globals.get("__name__")
        if module_name and module_name.startswith("__plugins__"):
            plugin_name = module_name[len("__plugins__") :]
            return plugin_name

        current_frame = current_frame.f_back

    raise RuntimeError("get_current_plugin() must be called from within a plugin module")


def get_current_plugin_setting(key: str) -> str | bool:
    plugin = get_current_plugin()
    return get_plugin_setting(plugin, key)
