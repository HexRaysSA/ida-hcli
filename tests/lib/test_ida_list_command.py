from rich.console import Console

from hcli.commands.ida import list as list_module


def _render_ida_list(monkeypatch, instances: dict[str, str], default_instance: str) -> str:
    console = Console(record=True, force_terminal=False, width=120)
    monkeypatch.setattr(list_module, "console", console)
    monkeypatch.setattr(list_module.config_store, "get_object", lambda key, default=None: instances if key == "ida.instances" else default)
    monkeypatch.setattr(
        list_module.config_store, "get_string", lambda key, default="": default_instance if key == "ida.default" else default
    )
    monkeypatch.setattr(list_module, "is_ida_dir", lambda path: path.exists() and path.name != "IDA Professional 9.2.app")

    list_module.list_instances.callback()

    return console.export_text()


def test_list_instances_places_default_first_and_recommends_latest(monkeypatch, tmp_path):
    ida_92 = tmp_path / "IDA Professional 9.2.app"
    ida_93 = tmp_path / "IDA Professional 9.3.app"
    ida_91 = tmp_path / "IDA Professional 9.1.app"
    ida_90 = tmp_path / "IDA Professional 9.0.app"

    ida_92.mkdir()
    ida_93.mkdir()
    ida_91.mkdir()

    output = _render_ida_list(
        monkeypatch,
        {
            "ida-pro-9.2": str(ida_92),
            "ida-pro-9.3": str(ida_93),
            "ida-pro-9.1": str(ida_91),
            "ida-pro-9.0": str(ida_90),
        },
        "ida-pro-9.1",
    )

    assert "ida-pro-9.1 (default)" in output
    assert output.index("ida-pro-9.1 (default)") < output.index("ida-pro-9.3")
    assert output.index("ida-pro-9.3") < output.index("ida-pro-9.2")
    assert output.index("ida-pro-9.2") < output.index("ida-pro-9.0")
    assert "Latest valid IDA installation is not the default." in output
    assert "Use 'hcli ida switch ida-pro-9.3' to update it." in output


def test_list_instances_omits_switch_hint_when_default_is_latest(monkeypatch, tmp_path):
    ida_92 = tmp_path / "IDA Professional 9.2.app"
    ida_93 = tmp_path / "IDA Professional 9.3.app"

    ida_92.mkdir()
    ida_93.mkdir()

    output = _render_ida_list(
        monkeypatch,
        {
            "ida-pro-9.2": str(ida_92),
            "ida-pro-9.3": str(ida_93),
        },
        "ida-pro-9.3",
    )

    assert "ida-pro-9.3 (default)" in output
    assert "Latest valid IDA installation is not the default." not in output
