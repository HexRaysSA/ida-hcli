from __future__ import annotations

import io
import json
import os
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest
import zstandard
from click.testing import CliRunner
from conftest import META_RECORD, FakeBackend, log_out, make_meta, make_pack, make_release, pack_key, use_backend

from hcli.commands.packs import packs
from hcli.commands.packs.doctor import Status, _check_sig_dir
from hcli.lib.packs.errors import IntegrityError, LocalIOError, UnsupportedArchError
from hcli.lib.packs.install import (
    MANIFEST_NAME,
    InstallAction,
    extract_sigs,
    install_dir,
    install_pack,
    plan_install,
    proc_dir,
    read_installed,
    sig_reference,
    work_dir,
)
from hcli.lib.packs.models import ReleasePack
from hcli.lib.packs.store import cached_pack_path

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
META = make_meta([META_RECORD])
SNAPSHOT = "7d96eefa-06f6-451e-9e69-e62412c12f4b"


def _archive(members: list[tuple[str, bytes]], *, extra: list[tarfile.TarInfo] | None = None) -> bytes:
    """A zstd tar of regular files (plus any raw ``extra`` members)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info)
    return zstandard.ZstdCompressor().compress(buffer.getvalue())


def _sigs(*shas: str) -> bytes:
    return _archive([(f"sigs/{sha}.sig", f"sig {sha}".encode()) for sha in shas])


def _pack(arch: str, payload: bytes, **overrides: Any) -> ReleasePack:
    return ReleasePack.model_validate(make_pack(arch, payload, META, fmt="flirt", **overrides))


def _write_archive(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "pack.tar.zst"
    path.write_bytes(payload)
    return path


def _listing(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


# layout


@pytest.mark.parametrize(
    ("arch", "proc"),
    [
        ("x86", "pc"),
        ("x86_64", "pc"),
        ("arm", "arm"),
        ("arm64", "arm"),
        ("arm64e", "arm"),
        ("arm64ec", "arm"),
        ("arm64_32", "arm"),
    ],
)
def test_proc_dir_and_layout(arch: str, proc: str, tmp_path: Path) -> None:
    pack = _pack(arch, b"x")
    assert proc_dir(pack) == proc
    assert install_dir(tmp_path, pack) == tmp_path / proc / "hcli-packs" / "msvc" / f"windows-all-{arch}"
    assert sig_reference(pack, SHA_A) == f"hcli-packs/msvc/windows-all-{arch}/{SHA_A}"


def test_unsupported_arch_names_the_supported_ones(tmp_path: Path) -> None:
    pack = _pack("generic", b"x")
    with pytest.raises(UnsupportedArchError) as info:
        install_dir(tmp_path, pack)
    assert info.value.kind == "unsupported_arch"
    assert "'generic'" in info.value.message
    assert "supported: arm, arm64, arm64_32, arm64e, arm64ec, x86, x86_64" in info.value.message
    # Planning never raises: the pack is planned as unsupported, without a folder.
    supported, unsupported = plan_install([_pack("x86", b"x"), pack], tmp_path)
    assert supported.action is InstallAction.INSTALL
    assert (unsupported.action, unsupported.target, unsupported.installed.problem) == (
        InstallAction.UNSUPPORTED,
        None,
        None,
    )


# extraction


def test_extract_sigs_writes_only_the_signatures(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path, _sigs(SHA_A, SHA_B))
    dest = tmp_path / "dest"
    dest.mkdir()
    assert extract_sigs(archive, dest, "pack") == 2
    assert _listing(dest) == [f"{SHA_A}.sig", f"{SHA_B}.sig"]
    assert (dest / f"{SHA_A}.sig").read_bytes() == f"sig {SHA_A}".encode()


def _symlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


def _hardlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    return info


def _directory(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    return info


@pytest.mark.parametrize(
    ("members", "extra"),
    [
        pytest.param([("sigs/../escape.sig", b"x")], [], id="traversal-dotdot"),
        pytest.param([("../escape.sig", b"x")], [], id="traversal-parent"),
        pytest.param([("/tmp/escape.sig", b"x")], [], id="absolute"),
        pytest.param([("sigs/sub/a.sig", b"x")], [], id="nested"),
        pytest.param([("sigs/readme.txt", b"x")], [], id="not-a-sig"),
        pytest.param([("other/a.sig", b"x")], [], id="outside-sigs"),
        pytest.param([("sigs/.hidden.sig", b"x")], [], id="unsafe-name"),
        pytest.param([("sigs/a.sig", b"x")], [], id="not-a-sha256"),
        pytest.param([(f"sigs/{SHA_A.upper()}.sig", b"x")], [], id="uppercase-sha256"),
        pytest.param([(f"./sigs/{SHA_A}.sig", b"x")], [], id="dot-prefix"),
        pytest.param([(f"sigs/{SHA_A}.sig", b"x")], [_symlink(f"sigs/{SHA_B}.sig", "/etc/passwd")], id="symlink"),
        pytest.param([(f"sigs/{SHA_A}.sig", b"x")], [_hardlink(f"sigs/{SHA_B}.sig", "/etc/passwd")], id="hardlink"),
        pytest.param([(f"sigs/{SHA_A}.sig", b"x")], [_directory("sigs")], id="directory"),
        pytest.param([(f"sigs/{SHA_A}.sig", b"x")], [_directory("sigs/")], id="directory-slash"),
        pytest.param([(f"sigs/{SHA_A}.sig", b"x"), (f"sigs/{SHA_A}.sig", b"y")], [], id="duplicate"),
    ],
)
def test_install_rejects_unsafe_members(
    tmp_path: Path, members: list[tuple[str, bytes]], extra: list[tarfile.TarInfo]
) -> None:
    archive = _write_archive(tmp_path, _archive(members, extra=extra))
    root = tmp_path / "sig"
    pack = _pack("x86_64", b"x")
    target = install_dir(root, pack)
    with pytest.raises(IntegrityError) as info:
        install_pack(archive, pack, SNAPSHOT, root)
    assert info.value.kind == "integrity"
    # Nothing that looks installed, and no work folder left behind.
    assert not target.exists()
    assert _listing(target.parent) == []
    assert _listing(work_dir(root)) == []
    assert not (tmp_path / "escape.sig").exists()


def test_install_rejects_an_empty_or_invalid_archive(tmp_path: Path) -> None:
    pack = _pack("x86_64", b"x")
    for payload in (_archive([]), b"not zstd at all"):
        with pytest.raises(IntegrityError):
            install_pack(_write_archive(tmp_path, payload), pack, SNAPSHOT, tmp_path / "sig")


# install and upgrade


def test_install_writes_sigs_and_manifest(tmp_path: Path) -> None:
    pack = _pack("x86_64", b"x")
    target = install_dir(tmp_path / "sig", pack)
    manifest = install_pack(_write_archive(tmp_path, _sigs(SHA_A, SHA_B)), pack, SNAPSHOT, tmp_path / "sig")
    assert _listing(target) == [MANIFEST_NAME, f"{SHA_A}.sig", f"{SHA_B}.sig"]
    assert (manifest.address, manifest.revision, manifest.sha256, manifest.sigs) == (
        pack.address,
        2,
        pack.sha256,
        2,
    )
    on_disk = json.loads((target / MANIFEST_NAME).read_text())
    assert on_disk["snapshot"] == SNAPSHOT and on_disk["format"] == "flirt"
    assert read_installed(target, pack).manifest == manifest


def test_upgrade_replaces_the_folder_atomically(tmp_path: Path) -> None:
    old = _pack("x86_64", b"old", revision=1)
    new = _pack("x86_64", b"new", revision=2)
    root = tmp_path / "sig"
    target = install_dir(root, old)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A, SHA_B)), old, SNAPSHOT, root)
    install_pack(_write_archive(tmp_path, _sigs(SHA_C)), new, SNAPSHOT, root)
    assert _listing(target) == [MANIFEST_NAME, f"{SHA_C}.sig"]
    manifest = read_installed(target, new).manifest
    assert manifest is not None and manifest.revision == 2
    # No aside or work folders left behind, and none ever inside IDA's <proc>/ tree.
    assert _listing(target.parent) == [target.name]
    assert _listing(root) == [".hcli-work", "pc"]
    assert _listing(work_dir(root)) == []


def _windows_rename(real: Any) -> Any:
    """``os.rename`` as on Windows: never onto an existing path, even an empty folder."""

    def rename(src: Any, dst: Any) -> None:
        if os.path.lexists(dst):
            raise FileExistsError(183, "Cannot create a file when that file already exists", str(dst))
        real(src, dst)

    return rename


def test_upgrade_with_windows_rename_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hcli.lib.packs.install.os.rename", _windows_rename(os.rename))
    old = _pack("x86_64", b"old", revision=1)
    new = _pack("x86_64", b"new", revision=2)
    root = tmp_path / "sig"
    target = install_dir(root, old)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), old, SNAPSHOT, root)
    install_pack(_write_archive(tmp_path, _sigs(SHA_B)), new, SNAPSHOT, root)
    assert _listing(target) == [MANIFEST_NAME, f"{SHA_B}.sig"]
    assert _listing(target.parent) == [target.name]
    assert _listing(work_dir(root)) == []


def test_failed_swap_keeps_the_previous_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _pack("x86_64", b"old", revision=1)
    new = _pack("x86_64", b"new", revision=2)
    root = tmp_path / "sig"
    target = install_dir(root, old)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), old, SNAPSHOT, root)
    # An install that is a week old: the aside copy must not inherit that age.
    week_ago = time.time() - 7 * 24 * 60 * 60
    os.utime(target, (week_ago, week_ago))
    real = os.rename
    aside_mtimes: list[float] = []

    def locked(src: Any, dst: Any) -> None:
        # Moving the old install aside works; moving the new one in fails (a locked file on Windows).
        if Path(src).name.endswith(".new"):
            aside_mtimes.extend(entry.stat().st_mtime for entry in work_dir(root).glob("*.old"))
            raise PermissionError(5, "Access is denied", str(dst))
        real(src, dst)

    monkeypatch.setattr("hcli.lib.packs.install.os.rename", locked)
    with pytest.raises(LocalIOError):
        install_pack(_write_archive(tmp_path, _sigs(SHA_B)), new, SNAPSHOT, root)
    assert _listing(target) == [MANIFEST_NAME, f"{SHA_A}.sig"]
    assert read_installed(target, old).manifest is not None
    assert _listing(target.parent) == [target.name]
    assert _listing(work_dir(root)) == []
    [mtime] = aside_mtimes
    assert mtime > time.time() - 60


def test_failed_restore_names_where_the_previous_install_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _pack("x86_64", b"old", revision=1)
    new = _pack("x86_64", b"new", revision=2)
    root = tmp_path / "sig"
    target = install_dir(root, old)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), old, SNAPSHOT, root)
    real = os.rename

    def locked(src: Any, dst: Any) -> None:
        # Moving aside works; moving the new one in, and the old one back, both fail.
        if Path(src).name.endswith(".new") or Path(dst) == target:
            raise PermissionError(5, "Access is denied", str(dst))
        real(src, dst)

    monkeypatch.setattr("hcli.lib.packs.install.os.rename", locked)
    with pytest.raises(LocalIOError) as info:
        install_pack(_write_archive(tmp_path, _sigs(SHA_B)), new, SNAPSHOT, root)
    [aside] = work_dir(root).glob("*.old")
    assert info.value.hint is not None and f"the previous install was left at {aside}" in info.value.hint
    assert _listing(aside) == [MANIFEST_NAME, f"{SHA_A}.sig"]
    assert not target.exists()


def test_install_sweeps_only_stale_work_folders(tmp_path: Path) -> None:
    root = tmp_path / "sig"
    work = work_dir(root)
    work.mkdir(parents=True)
    stale, fresh, other = work / "0123456789abcdef.old", work / "fedcba9876543210.new", work / "keep-me.old"
    for folder in (stale, fresh, other):
        folder.mkdir()
    two_days_ago = time.time() - 2 * 24 * 60 * 60
    for folder in (stale, other):
        os.utime(folder, (two_days_ago, two_days_ago))
    pack = _pack("x86_64", b"x")
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), pack, SNAPSHOT, root)
    assert _listing(work) == sorted([fresh.name, other.name])


@pytest.mark.skipif(os.name == "nt", reason="folders cannot be fsynced on Windows")
def test_install_fsyncs_files_and_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: set[tuple[int, int]] = set()
    real = os.fsync

    def fsync(fd: int) -> None:
        info = os.fstat(fd)
        synced.add((info.st_dev, info.st_ino))
        real(fd)

    monkeypatch.setattr("hcli.lib.packs.install.os.fsync", fsync)
    root = tmp_path / "sig"
    pack = _pack("x86_64", b"x")
    install_pack(_write_archive(tmp_path, _sigs(SHA_A, SHA_B)), pack, SNAPSHOT, root)
    target = install_dir(root, pack)
    # Renames keep inodes: the installed folder is the work folder that was synced.
    for path in (target / f"{SHA_A}.sig", target / f"{SHA_B}.sig", target / MANIFEST_NAME, target, target.parent):
        info = path.stat()
        assert (info.st_dev, info.st_ino) in synced, path


def test_extract_reports_an_archive_read_failure_as_a_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _write_archive(tmp_path, _sigs(SHA_A))
    real_open = Path.open

    class Failing(io.BufferedReader):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError(5, "Input/output error")

        def read1(self, size: int = -1) -> bytes:
            raise OSError(5, "Input/output error")

        def readinto(self, buffer: Any) -> int:
            raise OSError(5, "Input/output error")

    def opener(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if self == archive:
            return Failing(io.FileIO(str(self), "rb"))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opener)
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(LocalIOError) as info:
        extract_sigs(archive, dest, "pack")
    assert info.value.message.startswith(f"Could not read {archive}:")


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (None, "folder exists without a manifest"),
        (b"{not json", "manifest is corrupt"),
        (b'{"address": "msvc/windows-all-x86_64"}', "manifest is corrupt"),
    ],
)
def test_missing_or_corrupt_manifest_means_not_installed(tmp_path: Path, content: bytes | None, problem: str) -> None:
    pack = _pack("x86_64", b"x")
    root = tmp_path / "sig"
    target = install_dir(root, pack)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), pack, SNAPSHOT, root)
    manifest = target / MANIFEST_NAME
    if content is None:
        manifest.unlink()
    else:
        manifest.write_bytes(content)
    [item] = plan_install([pack], root)
    assert (item.action, item.installed.manifest, item.installed.problem) == (InstallAction.INSTALL, None, problem)


def test_plan_actions(tmp_path: Path) -> None:
    root = tmp_path / "sig"
    same = _pack("x86_64", b"same")
    older = _pack("arm64", b"old", revision=1)
    newer = _pack("arm64", b"new", revision=2)
    fresh = _pack("x86", b"fresh")
    ahead = _pack("x86", b"ahead", revision=3)
    behind = _pack("x86", b"behind", revision=2)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), same, SNAPSHOT, root)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), older, SNAPSHOT, root)
    plan = plan_install([same, newer, fresh], root)
    assert [(item.pack.arch, item.action) for item in plan] == [
        ("x86_64", InstallAction.UP_TO_DATE),
        ("arm64", InstallAction.UPGRADE),
        ("x86", InstallAction.INSTALL),
    ]
    assert plan[0].installed.problem is None and plan[2].installed.problem is None
    # An installed revision above stable is a downgrade, not an upgrade.
    install_pack(_write_archive(tmp_path, _sigs(SHA_A)), ahead, SNAPSHOT, root)
    [item] = plan_install([behind], root)
    assert item.action is InstallAction.DOWNGRADE


@pytest.mark.parametrize("change", ["delete", "add"])
def test_sig_count_mismatch_means_not_installed(tmp_path: Path, change: str) -> None:
    pack = _pack("x86_64", b"x")
    root = tmp_path / "sig"
    target = install_dir(root, pack)
    install_pack(_write_archive(tmp_path, _sigs(SHA_A, SHA_B)), pack, SNAPSHOT, root)
    if change == "delete":
        (target / f"{SHA_A}.sig").unlink()
        problem = "folder holds 1 signatures, its manifest records 2"
    else:
        (target / f"{SHA_C}.sig").write_bytes(b"stray")
        problem = "folder holds 3 signatures, its manifest records 2"
    [item] = plan_install([pack], root)
    assert (item.action, item.installed.manifest, item.installed.problem) == (InstallAction.INSTALL, None, problem)


# CLI


ARCHIVES = {"x86_64": _sigs(SHA_A, SHA_B), "arm64": _sigs(SHA_C)}


def _objects(archives: dict[str, bytes] = ARCHIVES) -> dict[str, bytes]:
    entries = [make_pack(arch, payload, META, fmt="flirt") for arch, payload in archives.items()]
    objects = {pack_key(entry, "flirt"): archives[entry["arch"]] for entry in entries}
    objects["flirt/release"] = json.dumps(make_release(entries, fmt="flirt")).encode()
    return objects


@pytest.fixture
def fake_repo(cache_root: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    backend = FakeBackend(_objects())
    use_backend(monkeypatch, backend)
    return backend


def _invoke(*args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(packs, ["flirt", *args], env=env, catch_exceptions=False)


def _json(*args: str, env: dict[str, str] | None = None) -> Any:
    result = _invoke(*args, "--json", env=env)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


X64 = "msvc/windows-all-x86_64"
ARM = "msvc/windows-all-arm64"


def test_install_defaults_to_the_ida_user_sig_folder(fake_repo: FakeBackend, tmp_path: Path) -> None:
    idausr = tmp_path / "idausr"
    assert not idausr.exists()  # Created on demand, like the plugins folder.
    installed = _json("install", X64)
    assert installed["sig_dir"] == str(idausr / "sig")
    assert (idausr / "sig" / "pc" / "hcli-packs" / "msvc" / "windows-all-x86_64" / MANIFEST_NAME).is_file()


def test_sig_dir_option_wins_over_the_ida_user_folder(fake_repo: FakeBackend, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    installed = _json("install", X64, "--sig-dir", str(elsewhere))
    assert installed["sig_dir"] == str(elsewhere)
    assert not (tmp_path / "idausr").exists()


@pytest.mark.parametrize("command", [["install", X64], ["sync"]])
def test_logged_out_install_touches_no_sig_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: list[str]
) -> None:
    log_out(monkeypatch)
    backend = FakeBackend(_objects())
    use_backend(monkeypatch, backend)
    result = _invoke(*command)
    assert result.exit_code == 1
    assert "not logged in" in result.output
    assert backend.reads == []
    assert not (tmp_path / "idausr").exists()


def test_install_then_noop_then_force(fake_repo: FakeBackend, tmp_path: Path, cache_root: Path) -> None:
    root = tmp_path / "sig"
    first = _invoke("install", X64, "--sig-dir", str(root))
    assert first.exit_code == 0, first.output
    target = root / "pc" / "hcli-packs" / "msvc" / "windows-all-x86_64"
    assert _listing(target) == [MANIFEST_NAME, f"{SHA_A}.sig", f"{SHA_B}.sig"]
    assert "2 sigs installed in" in first.output
    assert "hcli-packs/msvc/windows-all-x86_64/<sig>" in first.output
    # Downloaded to temp, not kept in the cache.
    assert not cached_pack_path(_pack("x86_64", ARCHIVES["x86_64"])).exists()

    again = _json("install", X64, env={"HCLI_IDAUSR": str(tmp_path)})
    [entry] = again["packs"]
    assert (entry["status"], entry["source"], entry["path"]) == ("already_installed", None, str(target))
    assert again["sig_dir"] == str(root) and again["snapshot"] == SNAPSHOT and again["unsupported"] == []

    forced = _json("install", X64, "--force", "--keep", "--sig-dir", str(root))
    [entry] = forced["packs"]
    assert (entry["status"], entry["source"], entry["sigs"]) == ("reinstalled", "downloaded", 2)
    assert cached_pack_path(_pack("x86_64", ARCHIVES["x86_64"])).is_file()


def test_install_dry_run_writes_nothing(fake_repo: FakeBackend, tmp_path: Path, cache_root: Path) -> None:
    root = tmp_path / "sig"
    output = _json("install", "msvc/*", "--dry-run", "--sig-dir", str(root))
    assert output["dry_run"] is True
    assert [(entry["address"], entry["action"], entry["source"]) for entry in output["packs"]] == [
        (X64, "install", "would_download"),
        (ARM, "install", "would_download"),
    ]
    assert not root.exists()
    assert not cache_root.exists()
    assert fake_repo.reads == ["flirt/release"]


def test_sync_plan_installs_and_upgrades(cache_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "sig"
    env = {"HCLI_IDAUSR": str(tmp_path)}
    old = FakeBackend(_objects({"x86_64": _sigs(SHA_A), "arm64": _sigs(SHA_C)}))
    new = FakeBackend(_objects({"x86_64": _sigs(SHA_B), "arm64": _sigs(SHA_C)}))
    with pytest.MonkeyPatch.context() as patch:
        use_backend(patch, old)
        first = _json("sync", env=env)
        assert [item["action"] for item in first["plan"]] == ["install", "install"]
        assert [entry["status"] for entry in first["packs"]] == ["installed", "installed"]
    with pytest.MonkeyPatch.context() as patch:
        use_backend(patch, new)
        dry = _json("sync", "--dry-run", env=env)
        plan = {item["address"]: item for item in dry["plan"]}
        assert plan[X64]["action"] == "upgrade"
        assert plan[X64]["installed"] == 2 and plan[X64]["installed_sha256"] != plan[X64]["stable_sha256"]
        assert plan[ARM]["action"] == "up_to_date"
        assert [entry["address"] for entry in dry["packs"]] == [X64]

        human = _invoke("sync", env=env)
        assert human.exit_code == 0, human.output
        assert "INSTALLED" in human.output and "install (upgrade)" in human.output
        target = root / "pc" / "hcli-packs" / "msvc" / "windows-all-x86_64"
        assert _listing(target) == [MANIFEST_NAME, f"{SHA_B}.sig"]

        done = _invoke("sync", env=env)
        assert "is up to date with snapshot 7d96eefa" in done.output
        assert "up to date" in done.output and "up_to_date" not in done.output


def test_sync_downgrades_an_install_ahead_of_stable(fake_repo: FakeBackend, tmp_path: Path) -> None:
    root = tmp_path / "sig"
    ahead = _pack("x86_64", b"ahead", revision=5)
    install_pack(_write_archive(tmp_path, _sigs(SHA_C)), ahead, SNAPSHOT, root)
    env = {"HCLI_IDAUSR": str(tmp_path)}
    dry = _json("sync", "--arch", "x86_64", "--dry-run", env=env)
    assert [(item["installed"], item["action"]) for item in dry["plan"]] == [(5, "downgrade")]
    human = _invoke("sync", "--arch", "x86_64", "--dry-run", env=env)
    assert "install (downgrade)" in human.output
    result = _json("sync", "--arch", "x86_64", env=env)
    [entry] = result["packs"]
    assert (entry["status"], entry["previous_revision"], entry["revision"]) == ("downgraded", 5, 2)


def test_sync_reinstalls_a_folder_missing_a_signature(fake_repo: FakeBackend, tmp_path: Path) -> None:
    root = tmp_path / "sig"
    env = {"HCLI_IDAUSR": str(tmp_path)}
    _json("sync", "--arch", "x86_64", env=env)
    target = root / "pc" / "hcli-packs" / "msvc" / "windows-all-x86_64"
    (target / f"{SHA_A}.sig").unlink()
    result = _invoke("sync", "--arch", "x86_64", env=env)
    assert result.exit_code == 0, result.output
    assert f"note: {X64}: folder holds 1 signatures, its manifest records 2; installing it again" in result.output
    assert _listing(target) == [MANIFEST_NAME, f"{SHA_A}.sig", f"{SHA_B}.sig"]


GENERIC = "msvc/windows-all-generic"
WITH_GENERIC = {**ARCHIVES, "generic": _sigs(SHA_C)}


@pytest.fixture
def generic_repo(cache_root: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    backend = FakeBackend(_objects(WITH_GENERIC))
    use_backend(monkeypatch, backend)
    return backend


def test_sync_skips_unsupported_packs(generic_repo: FakeBackend, tmp_path: Path) -> None:
    root = tmp_path / "sig"
    env = {"HCLI_IDAUSR": str(tmp_path)}
    dry = _json("sync", "--dry-run", env=env)
    plan = {item["address"]: item for item in dry["plan"]}
    assert (plan[GENERIC]["action"], plan[GENERIC]["path"]) == ("unsupported", None)
    assert [entry["address"] for entry in dry["packs"]] == [X64, ARM]

    human = _invoke("sync", env=env)
    assert human.exit_code == 0, human.output
    assert f"note: {GENERIC}: hcli cannot install arch 'generic'; skipped" in human.output
    assert "unsupported" in human.output
    assert not any("generic" in key for key in generic_repo.reads)
    assert _listing(root / "pc" / "hcli-packs" / "msvc") == ["windows-all-x86_64"]

    done = _json("sync", env=env)
    assert {item["action"] for item in done["plan"]} == {"up_to_date", "unsupported"}
    assert done["packs"] == []


def test_install_glob_skips_unsupported_but_an_exact_name_fails(generic_repo: FakeBackend, tmp_path: Path) -> None:
    root = tmp_path / "sig"
    globbed = _json("install", "msvc/*", "--sig-dir", str(root))
    assert [entry["address"] for entry in globbed["packs"]] == [X64, ARM]
    assert globbed["unsupported"] == [GENERIC]
    dry = _json("install", "msvc/*", "--dry-run", "--sig-dir", str(root))
    assert [entry["action"] for entry in dry["packs"]] == ["up_to_date", "up_to_date"]
    assert dry["unsupported"] == [GENERIC]

    named = _invoke("install", GENERIC, "--json", "--sig-dir", str(root))
    assert named.exit_code == 1
    assert json.loads(named.output)["kind"] == "unsupported_arch"


def test_sync_reinstalls_a_folder_with_a_corrupt_manifest(fake_repo: FakeBackend, tmp_path: Path) -> None:
    root = tmp_path / "sig"
    env = {"HCLI_IDAUSR": str(tmp_path)}
    _json("sync", "--arch", "x86_64", env=env)
    target = root / "pc" / "hcli-packs" / "msvc" / "windows-all-x86_64"
    (target / MANIFEST_NAME).write_text("garbage")
    result = _invoke("sync", "--arch", "x86_64", env=env)
    assert result.exit_code == 0, result.output
    assert f"note: {X64}: manifest is corrupt; installing it again" in result.output
    assert read_installed(target, _pack("x86_64", ARCHIVES["x86_64"])).manifest is not None


def test_list_shows_installs_of_the_ida_user_sig_folder(fake_repo: FakeBackend, tmp_path: Path) -> None:
    root = tmp_path / "sig"
    _json("install", X64, "--sig-dir", str(root))
    # An older build of the arm64 pack.
    stale = _pack("arm64", b"older build", revision=1)
    install_pack(_write_archive(tmp_path, _sigs(SHA_C)), stale, SNAPSHOT, root)

    # Another IDA user folder (the default one is empty): nothing installed there.
    plain = _json("list")
    assert plain["sig_dir"] == str(tmp_path / "idausr" / "sig")
    assert all(entry["installed"] is None for entry in plain["packs"])
    human = _invoke("list").output
    assert "✓r1" not in human and "✓r2" not in human

    env = {"HCLI_IDAUSR": str(tmp_path)}
    listed = _json("list", env=env)
    assert listed["sig_dir"] == str(root)
    installed = {entry["address"]: entry["installed"] for entry in listed["packs"]}
    assert installed[X64] is not None and installed[X64]["up_to_date"] is True
    assert installed[ARM] is not None and (installed[ARM]["revision"], installed[ARM]["up_to_date"]) == (1, False)

    human = _invoke("list", env=env).output
    assert "✓rN    installed in" in human
    assert "✓r2" in human and "✓r1" in human


def test_doctor_sig_dir_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check = _check_sig_dir()
    assert (check.status, check.detail) == (
        Status.WARN,
        f"{tmp_path / 'idausr' / 'sig'}: does not exist yet (install creates it)",
    )
    (tmp_path / "idausr" / "sig").parent.mkdir()
    (tmp_path / "idausr" / "sig").write_text("not a folder", encoding="utf-8")
    assert _check_sig_dir().status is Status.WARN
    monkeypatch.setenv("HCLI_IDAUSR", str(tmp_path))
    (tmp_path / "sig").mkdir()
    assert _check_sig_dir().status is Status.OK
