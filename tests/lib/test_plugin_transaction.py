"""Tests for the journaled install transaction."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from pathlib import Path

import pytest
from fixtures import *

from hcli.lib.ida import IDAConfigJson, PluginConfig, get_ida_config, set_ida_config
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.install import get_plugins_directory, get_trash_directory, sweep_trash
from hcli.lib.ida.plugin.transaction import (
    InstallTransaction,
    PreconditionChangedError,
    RollbackError,
    is_transaction_active,
)


def _metadata_with_settings() -> IDAMetadataDescriptor:
    return IDAMetadataDescriptor.model_validate(
        {
            "IDAMetadataDescriptorVersion": 1,
            "plugin": {
                "name": "plug",
                "version": "1.0.0",
                "entryPoint": "plug.py",
                "urls": {"repository": "https://github.com/test/plug"},
                "authors": [{"name": "Test", "email": "t@example.com"}],
                "settings": [
                    {"key": "token", "type": "string", "name": "Token", "documentation": "d", "required": False},
                    {"key": "flag", "type": "boolean", "name": "Flag", "documentation": "d", "required": False},
                ],
            },
        }
    )


def _write_plugin(path: Path, marker: str) -> None:
    path.mkdir(parents=True)
    (path / "ida-plugin.json").write_text(json.dumps({"marker": marker}))
    (path / "plug.py").write_text(marker)


def _txn() -> InstallTransaction:
    return InstallTransaction(get_plugins_directory())


def _stage(txn: InstallTransaction, marker: str) -> Path:
    staged = txn.make_staging_directory("plug")
    (staged / "ida-plugin.json").write_text(json.dumps({"marker": marker}))
    (staged / "plug.py").write_text(marker)
    return staged


def test_publish_then_rollback_removes_only_new_path(virtual_ida_environment):
    plugins = get_plugins_directory()
    _write_plugin(plugins / "other", "keep")
    txn = _txn()
    txn.publish_directory(_stage(txn, "new"), plugins / "plug")
    assert (plugins / "plug" / "plug.py").read_text() == "new"

    txn.rollback()

    assert not (plugins / "plug").exists()
    assert (plugins / "other" / "plug.py").read_text() == "keep"
    assert not is_transaction_active()


def test_replace_then_rollback_restores_prior_content(virtual_ida_environment):
    plugins = get_plugins_directory()
    _write_plugin(plugins / "plug", "old")
    (plugins / "plug" / "extra.txt").write_text("extra")
    txn = _txn()
    txn.replace_directory(_stage(txn, "new"), plugins / "plug")
    assert (plugins / "plug" / "plug.py").read_text() == "new"
    assert not (plugins / "plug" / "extra.txt").exists()

    txn.rollback()

    assert (plugins / "plug" / "plug.py").read_text() == "old"
    assert (plugins / "plug" / "extra.txt").read_text() == "extra"
    assert list(get_trash_directory(plugins).iterdir()) == []


def test_replace_missing_destination_is_precondition_error(virtual_ida_environment):
    plugins = get_plugins_directory()
    txn = _txn()
    staged = _stage(txn, "new")
    with pytest.raises(PreconditionChangedError):
        txn.replace_directory(staged, plugins / "plug")
    txn.rollback()
    assert staged.exists() is False


def test_commit_discards_checkpoints_and_keeps_new_content(virtual_ida_environment):
    plugins = get_plugins_directory()
    _write_plugin(plugins / "plug", "old")
    txn = _txn()
    txn.replace_directory(_stage(txn, "new"), plugins / "plug")
    assert any(p.name.startswith("plug.checkpoint-") for p in get_trash_directory(plugins).iterdir())

    txn.commit()

    assert (plugins / "plug" / "plug.py").read_text() == "new"
    assert list(get_trash_directory(plugins).iterdir()) == []
    assert not is_transaction_active()


def test_pth_rollback_restores_previous_and_removes_new(virtual_ida_environment, tmp_path):
    existing = tmp_path / "site" / "_hcli_editable_a.pth"
    existing.parent.mkdir()
    existing.write_bytes(b"/old/path\n")
    fresh = tmp_path / "site" / "_hcli_editable_b.pth"
    removed = tmp_path / "site" / "_hcli_editable_c.pth"
    removed.write_bytes(b"/gone\n")

    txn = _txn()
    txn.write_pth(existing, "/new/path\n")
    txn.write_pth(fresh, "/fresh\n")
    txn.remove_pth(removed)
    assert existing.read_bytes() == b"/new/path\n"
    assert fresh.exists()
    assert not removed.exists()

    txn.rollback()

    assert existing.read_bytes() == b"/old/path\n"
    assert not fresh.exists()
    assert removed.read_bytes() == b"/gone\n"


def test_config_key_rollback_for_existing_and_absent_keys(virtual_ida_environment):
    set_ida_config(IDAConfigJson(Plugins={"plug": PluginConfig(settings={"token": "before"})}))
    metadata = _metadata_with_settings()

    txn = _txn()
    txn.set_config_key("plug", "token", "after", metadata)
    txn.set_config_key("plug", "flag", True, metadata)
    config = get_ida_config()
    assert config.plugins["plug"].settings == {"token": "after", "flag": True}

    txn.rollback()

    config = get_ida_config()
    assert config.plugins["plug"].settings == {"token": "before"}


def test_config_key_rollback_removes_plugin_entry_it_created(virtual_ida_environment):
    metadata = _metadata_with_settings()
    txn = _txn()
    txn.set_config_key("plug", "token", "value", metadata)
    assert get_ida_config().plugins["plug"].settings == {"token": "value"}

    txn.rollback()

    assert "plug" not in get_ida_config().plugins


def test_config_key_precondition_detects_intervening_edit(virtual_ida_environment):
    set_ida_config(IDAConfigJson(Plugins={"plug": PluginConfig(settings={"token": "planned"})}))
    set_ida_config(IDAConfigJson(Plugins={"plug": PluginConfig(settings={"token": "edited"})}))
    metadata = _metadata_with_settings()
    txn = _txn()
    with pytest.raises(PreconditionChangedError):
        txn.set_config_key("plug", "token", "new", metadata, expected_existing=True, expected_value="planned")
    with pytest.raises(PreconditionChangedError):
        txn.set_config_key("plug", "flag", True, metadata, expected_existing=True, expected_value=False)
    assert get_ida_config().plugins["plug"].settings == {"token": "edited"}
    txn.rollback()


def test_config_key_rejects_wrong_type_and_writes_nothing(virtual_ida_environment):
    metadata = _metadata_with_settings()
    txn = _txn()
    with pytest.raises(ValueError):
        txn.set_config_key("plug", "flag", "yes", metadata)
    assert "plug" not in get_ida_config().plugins
    txn.rollback()


def test_savepoint_partial_rollback_keeps_earlier_entries(virtual_ida_environment):
    plugins = get_plugins_directory()
    _write_plugin(plugins / "b", "old-b")
    txn = _txn()
    txn.publish_directory(_stage(txn, "a"), plugins / "a")
    mark = txn.savepoint()
    txn.replace_directory(_stage(txn, "new-b"), plugins / "b")
    txn.publish_directory(_stage(txn, "c"), plugins / "c")

    txn.rollback_to(mark)

    assert (plugins / "a").is_dir()
    assert (plugins / "b" / "plug.py").read_text() == "old-b"
    assert not (plugins / "c").exists()

    txn.commit()
    assert (plugins / "a").is_dir()
    assert list(get_trash_directory(plugins).iterdir()) == []


def test_link_directory_rollback_restores_replaced_symlink(virtual_ida_environment, tmp_path):
    plugins = get_plugins_directory()
    old_source = tmp_path / "old-src"
    new_source = tmp_path / "new-src"
    old_source.mkdir()
    new_source.mkdir()
    (plugins / "plug").symlink_to(old_source, target_is_directory=True)

    txn = _txn()
    txn.link_directory(new_source, plugins / "plug")
    assert (plugins / "plug").resolve() == new_source.resolve()

    txn.rollback()

    assert (plugins / "plug").is_symlink()
    assert (plugins / "plug").resolve() == old_source.resolve()


def test_rollback_failure_retains_checkpoint_and_sweep_leaves_it(virtual_ida_environment):
    plugins = get_plugins_directory()
    _write_plugin(plugins / "plug", "old")
    txn = _txn()
    txn.replace_directory(_stage(txn, "new"), plugins / "plug")
    (plugins / "plug" / "sub").mkdir()
    (plugins / "plug" / "sub" / "f").write_text("x")
    os.chmod(plugins / "plug" / "sub", stat.S_IRUSR | stat.S_IXUSR)

    try:
        with pytest.raises(RollbackError) as excinfo:
            txn.rollback(RuntimeError("boom"))
    finally:
        os.chmod(plugins / "plug" / "sub", stat.S_IRWXU)

    assert isinstance(excinfo.value.original, RuntimeError)
    assert len(excinfo.value.retained_paths) == 1
    retained = excinfo.value.retained_paths[0]
    assert retained.parent.name.startswith("recovery-")
    assert (retained / "plug.py").read_text() == "old"
    assert not is_transaction_active()

    sweep_trash()
    assert (retained / "plug.py").read_text() == "old"


def test_sweep_is_skipped_while_transaction_active(virtual_ida_environment):
    txn = _txn()
    staged = _stage(txn, "new")
    sweep_trash()
    assert staged.exists()
    txn.rollback()
    assert not staged.exists()


def test_interrupt_during_step_rolls_back_and_propagates(virtual_ida_environment):
    plugins = get_plugins_directory()
    _write_plugin(plugins / "plug", "old")

    def run(txn: InstallTransaction) -> None:
        txn.replace_directory(_stage(txn, "new"), plugins / "plug")
        raise KeyboardInterrupt

    def guarded(txn: InstallTransaction) -> None:
        try:
            run(txn)
        except BaseException as e:
            txn.rollback(e)
            raise

    txn = _txn()
    with pytest.raises(KeyboardInterrupt):
        guarded(txn)

    assert (plugins / "plug" / "plug.py").read_text() == "old"
    assert list(get_trash_directory(plugins).iterdir()) == []
    assert not is_transaction_active()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as non-root")
def test_commit_survives_undeletable_checkpoint(virtual_ida_environment, caplog):
    plugins = get_plugins_directory()
    _write_plugin(plugins / "plug", "old")
    txn = _txn()
    txn.replace_directory(_stage(txn, "new"), plugins / "plug")
    trash = get_trash_directory(plugins)
    checkpoints = [p for p in trash.iterdir() if p.name.startswith("plug.checkpoint-")]
    assert len(checkpoints) == 1
    os.chmod(trash, stat.S_IRUSR | stat.S_IXUSR)

    try:
        with caplog.at_level(logging.WARNING, logger="hcli.lib.ida.plugin.transaction"):
            txn.commit()
    finally:
        os.chmod(trash, stat.S_IRWXU)

    assert txn.finished
    assert not is_transaction_active()
    assert (plugins / "plug" / "plug.py").read_text() == "new"
    assert checkpoints[0].exists()
    assert any("could not remove" in r.getMessage() for r in caplog.records)
