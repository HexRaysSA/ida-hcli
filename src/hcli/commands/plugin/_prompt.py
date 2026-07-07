"""Shared interactive settings prompting for plugin install and config."""

from __future__ import annotations

import questionary

from hcli.lib.console import console
from hcli.lib.ida.plugin import PluginSettingDescriptor
from hcli.lib.ida.plugin.settings import parse_setting_value


def prompt_plugin_settings(
    settings: list[PluginSettingDescriptor],
    existing_values: dict[str, str | bool] | None = None,
) -> dict[str, str | bool] | None:
    """Prompt the user for plugin settings interactively.

    Uses *existing_values* as defaults when available, falling back to
    descriptor defaults.  Empty-string answers for secret settings with
    existing values are omitted (meaning "keep the current value").

    Returns ``None`` if the user cancels the prompt (Ctrl-C).
    """
    if existing_values is None:
        existing_values = {}

    promptable = [s for s in settings if s.prompt]
    if not promptable:
        return {}

    plural = "s" if len(promptable) != 1 else ""
    console.print(f"configure {len(promptable)} setting{plural}:")

    questions: dict[str, questionary.Question] = {}
    secret_with_existing: set[str] = set()

    for setting in promptable:
        existing = existing_values.get(setting.key)

        if setting.type == "boolean":
            if isinstance(existing, bool):
                default_bool = existing
            elif isinstance(setting.default, bool):
                default_bool = setting.default
            else:
                default_bool = False
            questions[setting.key] = questionary.confirm(
                message=setting.name,
                default=default_bool,
            )

        elif setting.choices:
            if existing is not None:
                default_str = str(existing)
            elif setting.default is not None:
                default_str = str(setting.default)
            else:
                default_str = setting.choices[0]
            questions[setting.key] = questionary.select(
                message=setting.name,
                choices=setting.choices,
                default=default_str,
            )

        else:
            allow_empty_for_secret = existing is not None and setting.secret

            def make_validator(s, _allow_empty=allow_empty_for_secret):
                def validate_func(value: str):
                    if _allow_empty and not value:
                        return True
                    if not s.required and not value:
                        return True
                    if s.required and not value:
                        return "This field is required"
                    try:
                        parsed = parse_setting_value(s, value)
                        s.validate_value(parsed)
                        return True
                    except ValueError as e:
                        return str(e)

                return validate_func

            if setting.secret:
                message = setting.name
                if existing is not None:
                    message = f"{setting.name} (leave blank to keep current)"
                    secret_with_existing.add(setting.key)
                questions[setting.key] = questionary.password(
                    message=message,
                    validate=make_validator(setting),
                )
            else:
                if existing is not None:
                    default_str = str(existing)
                elif setting.default is not None:
                    default_str = str(setting.default)
                else:
                    default_str = ""
                questions[setting.key] = questionary.text(
                    message=setting.name,
                    default=default_str,
                    validate=make_validator(setting),
                )

    answers = questionary.form(**questions).ask()
    if answers is None:
        return None

    for key in list(answers.keys()):
        if key in secret_with_existing and answers[key] == "":
            del answers[key]

    return answers
