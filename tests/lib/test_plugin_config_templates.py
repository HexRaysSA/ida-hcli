"""Tests for plugin config templates copied into $IDAUSR/cfg."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from fixtures import *
from fixtures import make_test_install_context
from pydantic import ValidationError

from hcli.lib.ida import get_ida_user_dir
from hcli.lib.ida.plugin import (
    ConfigTemplate,
    IDAMetadataDescriptor,
    get_metadata_from_plugin_archive,
    validate_config_templates_in_plugin_directory,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.config_templates import (
    ActionKind,
    DestinationKind,
    DestinationState,
    TemplateFile,
    get_config_template_actions,
    validate_config_template_destinations,
)
from hcli.lib.ida.plugin.install import (
    apply_install,
    apply_upgrade,
    install_plugin_archive,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.plugin.result import InstallStatus

HOST = "https://github.com/test/test-plugin"


def make_metadata(name: str, version: str, templates: list[dict] | None = None, **extra) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": HOST},
        "authors": [{"name": "Test", "email": "test@example.com"}],
        **extra,
    }
    if templates is not None:
        plugin["configTemplates"] = templates
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def make_plugin_zip(
    name: str,
    version: str,
    files: dict[str, bytes],
    templates: list[dict],
    components: dict[str, tuple[dict[str, bytes], list[dict]]] | None = None,
) -> bytes:
    """A plugin archive whose root is `<name>/`, with components in `<name>/<component>/`."""
    buf = io.BytesIO()
    components = components or {}
    extra = {"components": list(components)} if components else {}
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(make_metadata(name, version, templates, **extra)))
        zf.writestr(f"{name}/{name}.py", "# plugin")
        for path, content in files.items():
            zf.writestr(f"{name}/{path}", content)
        for component, (component_files, component_templates) in components.items():
            prefix = f"{name}/{component}"
            zf.writestr(f"{prefix}/ida-plugin.json", json.dumps(make_metadata(component, version, component_templates)))
            zf.writestr(f"{prefix}/{component}.py", "# component")
            for path, content in component_files.items():
                zf.writestr(f"{prefix}/{path}", content)
    return buf.getvalue()


def make_deob_zip(version: str, cfg: bytes, patterns: bytes | None = b"patterns\n") -> bytes:
    files = {"defaults/deob.cfg": cfg}
    templates = [{"source": "defaults/deob.cfg", "destination": "$IDAUSR/cfg/deob.cfg"}]
    if patterns is not None:
        files["defaults/patterns.txt"] = patterns
        templates.append({"source": "defaults/patterns.txt", "destination": "$IDAUSR/cfg/deob/patterns.txt"})
    return make_plugin_zip("deob", version, files, templates)


def get_cfg_dir() -> Path:
    return get_ida_user_dir() / "cfg"


@pytest.mark.parametrize(
    "destination",
    [
        "$IDAUSR/cfg/deob.cfg",
        "$IDAUSR/cfg/deob.tar.gz",
        "$IDAUSR/cfg/deob/patterns.txt",
        "$IDAUSR/cfg/deob/sub/dir/patterns",
        "$IDAUSR/cfg/My-Plugin_2.cfg",
        "$IDAUSR/cfg/golang/patterns.txt",
        "$IDAUSR/cfg/idapython-extras.cfg",
    ],
)
def test_config_template_accepts_destination(destination: str):
    template = ConfigTemplate(source="defaults/x", destination=destination)
    assert template.destination_parts[0] == "cfg"


@pytest.mark.parametrize(
    ("destination", "message"),
    [
        ("cfg/deob.cfg", "must start with '$IDAUSR/'"),
        ("${IDAUSR}/cfg/deob.cfg", "must start with '$IDAUSR/'"),
        ("/etc/passwd", "must start with '$IDAUSR/'"),
        ("$IDAUSR/cfg/deob/", "must not end with '/'"),
        ("$IDAUSR/cfg//deob.cfg", "empty path components"),
        ("$IDAUSR/cfg/deob/../ida.cfg", "'.' or '..'"),
        ("$IDAUSR/cfg/./deob.cfg", "'.' or '..'"),
        ("$IDAUSR/../cfg/deob.cfg", "'.' or '..'"),
        ("$IDAUSR/cfg/deob/\u00e9.cfg", "ASCII"),
        ("$IDAUSR/cfg/deob\\x.cfg", "'/' as the path separator"),
        ("$IDAUSR/cfg/deob/c:x.cfg", "not valid in a path"),
        ("$IDAUSR/cfg/deob/a\tb.cfg", "not valid in a path"),
        ("$IDAUSR/cfg/deob/x.", "must not end with '.' or ' '"),
        ("$IDAUSR/cfg/deob/x ", "must not end with '.' or ' '"),
        ("$IDAUSR/cfg/deob/NUL.txt", "reserved on Windows"),
        ("$IDAUSR/cfg/deob/com1", "reserved on Windows"),
        ("$IDAUSR/cfg/deob/COM0.cfg", "reserved on Windows"),
        ("$IDAUSR/cfg/deob/lpt0", "reserved on Windows"),
        ("$IDAUSR/cfg/deob/CONIN$", "reserved on Windows"),
        ("$IDAUSR/cfg/deob/NUL .txt", "reserved on Windows"),
        ("$IDAUSR/cfg/ida.cfg", "config file that IDA ships"),
        ("$IDAUSR/cfg/hexrays.cfg", "config file that IDA ships"),
        ("$IDAUSR/cfg/IDAGUI.cfg", "config file that IDA ships"),
        ("$IDAUSR/cfg/golang.json", "config file that IDA ships"),
        ("$IDAUSR/cfg/ghidra_patterns/x86/x86_patterns.xml", "config file that IDA ships"),
        ("$IDAUSR/plugins/deob/deob.cfg", "must be under $IDAUSR/cfg/"),
        ("$IDAUSR/idapythonrc.py", "must be under $IDAUSR/cfg/"),
        ("$IDAUSR/cfg", "must name a file below"),
        ("$IDAUSR/cfg/deob", "must be named '<plugin>.<suffix>'"),
        ("$IDAUSR/cfg/_deob.cfg", "where <plugin> is a plugin name"),
        ("$IDAUSR/cfg/de$ob/x.cfg", "where <plugin> is a plugin name"),
    ],
)
def test_config_template_rejects_destination(destination: str, message: str):
    with pytest.raises(ValidationError) as excinfo:
        ConfigTemplate(source="defaults/x", destination=destination)
    assert message in str(excinfo.value)


@pytest.mark.parametrize("source", ["", "/etc/passwd", "../outside.cfg", "defaults/../../outside.cfg"])
def test_config_template_rejects_source(source: str):
    with pytest.raises(ValidationError):
        ConfigTemplate(source=source, destination="$IDAUSR/cfg/deob.cfg")


def test_metadata_rejects_duplicate_destinations_ignoring_case():
    templates = [
        {"source": "a.cfg", "destination": "$IDAUSR/cfg/deob.cfg"},
        {"source": "b.cfg", "destination": "$IDAUSR/cfg/DEOB.cfg"},
    ]
    with pytest.raises(ValidationError, match="must be unique"):
        IDAMetadataDescriptor.model_validate(make_metadata("deob", "1.0.0", templates))


def validate_archive(zip_data: bytes, name: str) -> None:
    metadata_path, metadata = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, metadata_path, metadata)


def test_archive_validation_rejects_missing_source():
    buf = make_plugin_zip("deob", "1.0.0", {}, [{"source": "defaults/deob.cfg", "destination": "$IDAUSR/cfg/deob.cfg"}])
    with pytest.raises(ValueError, match="source not found in archive"):
        validate_archive(buf, "deob")


@pytest.mark.parametrize("destination", ["$IDAUSR/cfg/deob-extra.cfg", "$IDAUSR/cfg/other/x"])
def test_archive_validation_rejects_destination_of_other_plugin(destination: str):
    buf = make_plugin_zip("deob", "1.0.0", {"a.cfg": b"x"}, [{"source": "a.cfg", "destination": destination}])
    with pytest.raises(ValueError, match="must start with \\$IDAUSR/cfg/deob/"):
        validate_archive(buf, "deob")


def test_archive_validation_accepts_destination_owner_in_other_case():
    buf = make_plugin_zip(
        "deob", "1.0.0", {"a.cfg": b"x"}, [{"source": "a.cfg", "destination": "$IDAUSR/cfg/Deob.cfg"}]
    )
    validate_archive(buf, "deob")


def make_suite_zip(component_destination: str) -> bytes:
    return make_plugin_zip(
        "suite",
        "1.0.0",
        {},
        [],
        components={
            "part": ({"part.cfg": b"part"}, [{"source": "part.cfg", "destination": component_destination}]),
            "other": ({}, []),
        },
    )


@pytest.mark.parametrize("destination", ["$IDAUSR/cfg/part.cfg", "$IDAUSR/cfg/suite/part.cfg", "$IDAUSR/cfg/SUITE.cfg"])
def test_component_may_use_its_own_or_its_suite_name(destination: str):
    buf = make_suite_zip(destination)
    validate_archive(buf, "suite")
    validate_archive(buf, "part")


def test_component_may_not_use_sibling_name():
    buf = make_suite_zip("$IDAUSR/cfg/other.cfg")
    with pytest.raises(ValueError, match="must start with \\$IDAUSR/cfg/part/"):
        validate_archive(buf, "part")


def test_component_may_not_use_name_of_unrelated_manifest_that_lists_it():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        templates = [{"source": "a.cfg", "destination": "$IDAUSR/cfg/victim.cfg"}]
        zf.writestr("deob/ida-plugin.json", json.dumps(make_metadata("deob", "1.0.0", templates)))
        zf.writestr("deob/deob.py", "# plugin")
        zf.writestr("deob/a.cfg", "x")
        zf.writestr("victim/ida-plugin.json", json.dumps(make_metadata("victim", "1.0.0", components=["deob"])))
        zf.writestr("victim/victim.py", "# plugin")

    with pytest.raises(ValueError, match="must start with \\$IDAUSR/cfg/deob/"):
        validate_archive(buf.getvalue(), "deob")


def test_directory_validation_requires_component_directory_named_after_component(tmp_path: Path):
    with zipfile.ZipFile(io.BytesIO(make_suite_zip("$IDAUSR/cfg/suite/part.cfg"))) as zf:
        zf.extractall(tmp_path)

    component_dir = tmp_path / "suite" / "renamed"
    (tmp_path / "suite" / "part").rename(component_dir)
    metadata = IDAMetadataDescriptor.model_validate_json((component_dir / "ida-plugin.json").read_text())
    with pytest.raises(ValueError, match="must start with \\$IDAUSR/cfg/part/"):
        validate_config_templates_in_plugin_directory(component_dir, metadata)


def test_directory_validation_finds_suite_from_parent_manifest(tmp_path: Path):
    with zipfile.ZipFile(io.BytesIO(make_suite_zip("$IDAUSR/cfg/suite/part.cfg"))) as zf:
        zf.extractall(tmp_path)

    component_dir = tmp_path / "suite" / "part"
    metadata = IDAMetadataDescriptor.model_validate_json((component_dir / "ida-plugin.json").read_text())
    validate_config_templates_in_plugin_directory(component_dir, metadata)

    (component_dir / "part.cfg").unlink()
    with pytest.raises(ValueError, match="source not found in directory"):
        validate_config_templates_in_plugin_directory(component_dir, metadata)


def test_directory_validation_rejects_suite_name_without_suite(tmp_path: Path):
    with zipfile.ZipFile(io.BytesIO(make_suite_zip("$IDAUSR/cfg/suite/part.cfg"))) as zf:
        zf.extractall(tmp_path)

    component_dir = tmp_path / "suite" / "part"
    (tmp_path / "suite" / "ida-plugin.json").unlink()
    metadata = IDAMetadataDescriptor.model_validate_json((component_dir / "ida-plugin.json").read_text())
    with pytest.raises(ValueError, match="must start with \\$IDAUSR/cfg/part/"):
        validate_config_templates_in_plugin_directory(component_dir, metadata)


def template(content: bytes, destination: str = "$IDAUSR/cfg/deob.cfg") -> TemplateFile:
    config_template = ConfigTemplate(source="defaults/deob.cfg", destination=destination)
    return TemplateFile(
        plugin="deob",
        destination=destination,
        destination_parts=config_template.destination_parts,
        source=Path("defaults/deob.cfg"),
        content=content,
    )


MISSING = DestinationState(DestinationKind.MISSING)
UNMANAGED = DestinationState(DestinationKind.UNMANAGED)


def file(content: bytes) -> DestinationState:
    return DestinationState(DestinationKind.FILE, content)


@pytest.mark.parametrize(
    ("old", "new", "state", "expected"),
    [
        # install
        (None, b"new", MISSING, ActionKind.WRITE),
        (None, b"new", file(b"new"), None),
        (None, b"new", file(b"other"), ActionKind.KEEP),
        (None, b"new", UNMANAGED, ActionKind.SKIP_UNMANAGED),
        # upgrade
        (b"old", b"new", file(b"old"), ActionKind.REPLACE),
        (b"old", b"new", file(b"new"), None),
        (b"old", b"new", MISSING, ActionKind.WRITE),
        (b"same", b"same", file(b"edited"), None),
        (b"old", b"new", file(b"edited"), ActionKind.KEEP_OUTDATED),
        (b"old", b"new", UNMANAGED, ActionKind.SKIP_UNMANAGED),
        # removed entry, and uninstall
        (b"old", None, file(b"old"), ActionKind.DELETE),
        (b"old", None, file(b"edited"), ActionKind.KEEP),
        (b"old", None, MISSING, None),
        (b"old", None, UNMANAGED, ActionKind.SKIP_UNMANAGED),
    ],
)
def test_get_config_template_actions(
    old: bytes | None, new: bytes | None, state: DestinationState, expected: ActionKind | None
):
    olds = [template(old)] if old is not None else []
    news = [template(new)] if new is not None else []
    actions = get_config_template_actions(olds, news, {"$IDAUSR/cfg/deob.cfg": state})
    assert [action.kind for action in actions] == ([expected] if expected else [])


def test_validate_config_template_destinations():
    suite = IDAMetadataDescriptor.model_validate(
        make_metadata("suite", "1.0.0", [{"source": "a", "destination": "$IDAUSR/cfg/suite/a.cfg"}])
    )
    part = IDAMetadataDescriptor.model_validate(
        make_metadata("part", "1.0.0", [{"source": "a", "destination": "$IDAUSR/cfg/Suite/A.cfg"}])
    )
    validate_config_template_destinations([suite], {})
    with pytest.raises(ValueError, match="declared by both 'suite' and 'part'"):
        validate_config_template_destinations([suite, part], {})
    with pytest.raises(ValueError, match="already declared by installed plugin 'old'"):
        validate_config_template_destinations([suite], {"$idausr/cfg/suite/a.cfg": "old"})


def test_install_writes_copies(virtual_ida_environment):
    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", make_test_install_context())

    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"cfg v1"
    assert (get_cfg_dir() / "deob" / "patterns.txt").read_bytes() == b"patterns\n"


def test_install_keeps_existing_different_file(virtual_ida_environment):
    get_cfg_dir().mkdir(parents=True)
    (get_cfg_dir() / "deob.cfg").write_bytes(b"mine")

    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", make_test_install_context())

    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"mine"
    assert (get_cfg_dir() / "deob" / "patterns.txt").is_file()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_destination_is_never_touched(virtual_ida_environment, tmp_path: Path):
    target = tmp_path / "elsewhere.cfg"
    target.write_bytes(b"cfg v1")
    get_cfg_dir().mkdir(parents=True)
    (get_cfg_dir() / "deob.cfg").symlink_to(target)

    ctx = make_test_install_context()
    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", ctx)
    upgrade_plugin_archive(make_deob_zip("2.0.0", b"cfg v2"), "deob", ctx)
    uninstall_plugin("deob")

    assert (get_cfg_dir() / "deob.cfg").is_symlink()
    assert target.read_bytes() == b"cfg v1"


def test_install_failure_to_write_rolls_back(virtual_ida_environment):
    get_cfg_dir().mkdir(parents=True)
    (get_cfg_dir() / "deob").write_bytes(b"a file where a directory is needed")

    with pytest.raises(Exception, match="failed to write"):
        install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", make_test_install_context())

    assert not is_plugin_installed("deob")
    assert not (get_cfg_dir() / "deob.cfg").exists()


def test_apply_install_failure_to_write_rolls_back(virtual_ida_environment):
    get_cfg_dir().mkdir(parents=True)
    (get_cfg_dir() / "deob").write_bytes(b"a file where a directory is needed")
    buf = make_deob_zip("1.0.0", b"cfg v1")
    _, metadata = get_metadata_from_plugin_archive(buf, "deob")

    result = apply_install(source=buf, plugin_name="deob", metadata=metadata, ctx=make_test_install_context())

    assert result.status == InstallStatus.FAILED
    assert result.reason is not None
    assert "failed to write" in result.reason
    assert not is_plugin_installed("deob")
    assert not (get_cfg_dir() / "deob.cfg").exists()


def test_upgrade_replaces_unedited_and_keeps_edited(virtual_ida_environment):
    ctx = make_test_install_context()
    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1", b"patterns v1"), "deob", ctx)
    (get_cfg_dir() / "deob" / "patterns.txt").write_bytes(b"patterns v1 + mine")

    upgrade_plugin_archive(make_deob_zip("2.0.0", b"cfg v2", b"patterns v2"), "deob", ctx)

    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"cfg v2"
    assert (get_cfg_dir() / "deob" / "patterns.txt").read_bytes() == b"patterns v1 + mine"


def test_upgrade_writes_deleted_copy_again(virtual_ida_environment):
    ctx = make_test_install_context()
    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", ctx)
    (get_cfg_dir() / "deob.cfg").unlink()

    upgrade_plugin_archive(make_deob_zip("2.0.0", b"cfg v1"), "deob", ctx)

    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"cfg v1"


def test_upgrade_deletes_copy_of_removed_entry(virtual_ida_environment):
    ctx = make_test_install_context()
    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", ctx)

    upgrade_plugin_archive(make_deob_zip("2.0.0", b"cfg v1", patterns=None), "deob", ctx)

    assert (get_cfg_dir() / "deob.cfg").is_file()
    assert not (get_cfg_dir() / "deob").exists()


def test_apply_upgrade_updates_copies(virtual_ida_environment):
    ctx = make_test_install_context()
    v1 = make_deob_zip("1.0.0", b"cfg v1")
    v2 = make_deob_zip("2.0.0", b"cfg v2")
    _, meta1 = get_metadata_from_plugin_archive(v1, "deob")
    _, meta2 = get_metadata_from_plugin_archive(v2, "deob")

    assert apply_install(source=v1, plugin_name="deob", metadata=meta1, ctx=ctx).status == InstallStatus.SUCCESS
    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"cfg v1"

    assert apply_upgrade(zip_data=v2, plugin_name="deob", metadata=meta2, ctx=ctx).status == InstallStatus.SUCCESS
    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"cfg v2"


def test_uninstall_deletes_unedited_and_keeps_edited(virtual_ida_environment):
    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", make_test_install_context())
    (get_cfg_dir() / "deob.cfg").write_bytes(b"cfg v1 + mine")

    uninstall_plugin("deob")

    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"cfg v1 + mine"
    assert not (get_cfg_dir() / "deob").exists()
    assert get_cfg_dir().is_dir()


def test_uninstall_keeps_directory_with_user_files(virtual_ida_environment):
    install_plugin_archive(make_deob_zip("1.0.0", b"cfg v1"), "deob", make_test_install_context())
    (get_cfg_dir() / "deob" / "mine.txt").write_bytes(b"mine")

    uninstall_plugin("deob")

    assert not (get_cfg_dir() / "deob.cfg").exists()
    assert not (get_cfg_dir() / "deob" / "patterns.txt").exists()
    assert (get_cfg_dir() / "deob" / "mine.txt").is_file()


def test_suite_installs_and_removes_component_copies(virtual_ida_environment):
    ctx = make_test_install_context()
    install_plugin_archive(make_suite_zip("$IDAUSR/cfg/suite/part.cfg"), "suite", ctx)

    assert (get_cfg_dir() / "suite" / "part.cfg").read_bytes() == b"part"

    uninstall_plugin("suite")

    assert not (get_cfg_dir() / "suite").exists()


def test_suite_rejects_destination_declared_twice(virtual_ida_environment):
    buf = make_plugin_zip(
        "suite",
        "1.0.0",
        {"a.cfg": b"a"},
        [{"source": "a.cfg", "destination": "$IDAUSR/cfg/suite/a.cfg"}],
        components={"part": ({"a.cfg": b"a"}, [{"source": "a.cfg", "destination": "$IDAUSR/cfg/suite/a.cfg"}])},
    )

    with pytest.raises(ValueError, match="declared by both"):
        install_plugin_archive(buf, "suite", make_test_install_context())

    assert not is_plugin_installed("suite")
    assert not (get_cfg_dir() / "suite").exists()


def make_inline_suite_zip(component_templates: list[dict], files: dict[str, bytes]) -> bytes:
    """A suite whose manifest lists the expanded metadata of component `c`, which has no `ida-plugin.json`."""
    component = make_metadata("c", "1.0.0", component_templates)
    suite = make_metadata("suite", "1.0.0", components=[component])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("suite/ida-plugin.json", json.dumps(suite))
        zf.writestr("suite/suite.py", "# plugin")
        zf.writestr("suite/c/c.py", "# component")
        for path, content in files.items():
            zf.writestr(f"suite/c/{path}", content)
    return buf.getvalue()


def test_suite_with_inline_component_installs_and_removes_copies(virtual_ida_environment):
    templates = [{"source": "c.cfg", "destination": "$IDAUSR/cfg/suite/c.cfg"}]
    install_plugin_archive(make_inline_suite_zip(templates, {"c.cfg": b"c"}), "suite", make_test_install_context())

    assert (get_cfg_dir() / "suite" / "c.cfg").read_bytes() == b"c"

    uninstall_plugin("suite")

    assert not (get_cfg_dir() / "suite").exists()


def test_install_validates_component_manifest_that_is_on_disk(virtual_ida_environment):
    templates = [{"source": "c.cfg", "destination": "$IDAUSR/cfg/victim.cfg"}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "suite/ida-plugin.json",
            json.dumps(make_metadata("suite", "1.0.0", components=[make_metadata("c", "1.0.0")])),
        )
        zf.writestr("suite/suite.py", "# plugin")
        zf.writestr("suite/c/ida-plugin.json", json.dumps(make_metadata("c", "1.0.0", templates)))
        zf.writestr("suite/c/c.py", "# component")
        zf.writestr("suite/c/c.cfg", "x")

    with pytest.raises(ValueError, match="must start with \\$IDAUSR/cfg/c/"):
        install_plugin_archive(buf.getvalue(), "suite", make_test_install_context())

    assert not is_plugin_installed("suite")
    assert not (get_cfg_dir() / "victim.cfg").exists()


def test_component_installed_alone_may_not_use_suite_name(virtual_ida_environment):
    buf = make_suite_zip("$IDAUSR/cfg/suite/part.cfg")

    with pytest.raises(ValueError, match="must start with \\$IDAUSR/cfg/part/"):
        install_plugin_archive(buf, "part", make_test_install_context())

    assert not is_plugin_installed("part")
    assert not (get_cfg_dir() / "suite").exists()


def test_upgrade_moves_copy_when_destination_changes_case(virtual_ida_environment):
    def make_zip(version: str, destination: str) -> bytes:
        return make_plugin_zip("deob", version, {"d.cfg": b"cfg"}, [{"source": "d.cfg", "destination": destination}])

    ctx = make_test_install_context()
    install_plugin_archive(make_zip("1.0.0", "$IDAUSR/cfg/Deob.cfg"), "deob", ctx)
    assert os.listdir(get_cfg_dir()) == ["Deob.cfg"]

    upgrade_plugin_archive(make_zip("2.0.0", "$IDAUSR/cfg/deob.cfg"), "deob", ctx)

    assert os.listdir(get_cfg_dir()) == ["deob.cfg"]
    assert (get_cfg_dir() / "deob.cfg").read_bytes() == b"cfg"
