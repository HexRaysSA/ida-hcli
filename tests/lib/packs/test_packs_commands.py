from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from click.testing import CliRunner
from conftest import (
    META_RECORD,
    FakeBackend,
    contents_key,
    log_out,
    make_meta,
    make_pack,
    make_release,
    pack_key,
    sha256_hex,
    use_backend,
)

from hcli.commands.packs import packs
from hcli.commands.packs.common import format_content
from hcli.commands.packs.doctor import Status, _run_checks
from hcli.commands.packs.get import output_targets
from hcli.commands.packs.show import _version_key, short_library_name
from hcli.lib.packs.errors import CredentialsError, InvalidReleaseError, RequestRefused
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.server import API_KEY_ENV, CA_CERT_ENV, SERVER_ENV

LUMINA_PAYLOAD = b"lumina pack bytes" * 3
FLIRT_PAYLOAD = b"flirt pack bytes" * 3
META = make_meta([META_RECORD])


def _objects() -> dict[str, bytes]:
    """Both formats publish msvc/windows-all-x86 under the same filename."""
    lumina = make_pack("x86", LUMINA_PAYLOAD, META)
    flirt = make_pack("x86", FLIRT_PAYLOAD, META, fmt="flirt")
    return {
        "lumina/release": json.dumps(make_release([lumina])).encode(),
        "flirt/release": json.dumps(make_release([flirt], fmt="flirt")).encode(),
        pack_key(lumina): LUMINA_PAYLOAD,
        pack_key(flirt, "flirt"): FLIRT_PAYLOAD,
        contents_key(lumina): META,
        contents_key(flirt, "flirt"): META,
    }


@pytest.fixture
def fake_repo(cache_root: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Commands see a PackRepository over an in-memory backend."""
    backend = FakeBackend(_objects())
    use_backend(monkeypatch, backend)
    return backend


def _invoke(*args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(packs, list(args), env=env, catch_exceptions=False)


# get


def test_get_writes_formats_side_by_side(fake_repo: FakeBackend, tmp_path: Path) -> None:
    out = tmp_path / "out"
    for fmt in ("lumina", "flirt"):
        result = _invoke(fmt, "get", "msvc/windows-all-x86", "-o", str(out), "--json")
        assert result.exit_code == 0, result.output
        [entry] = json.loads(result.output)["packs"]
        assert entry["path"] == str(out / fmt / "msvc" / "windows-all-x86-r2.tar.zst")

    assert (out / "lumina/msvc/windows-all-x86-r2.tar.zst").read_bytes() == LUMINA_PAYLOAD
    assert (out / "flirt/msvc/windows-all-x86-r2.tar.zst").read_bytes() == FLIRT_PAYLOAD
    assert fake_repo.closed


# each format group fixes its format


def _release_reads(backend: FakeBackend) -> set[str]:
    return {key for key in backend.reads if key.endswith("/release")}


@pytest.mark.parametrize("fmt", ["lumina", "flirt"])
def test_show_reads_its_groups_release(fake_repo: FakeBackend, fmt: str) -> None:
    result = _invoke(fmt, "show", "msvc/windows-all-x86", "--json")
    assert result.exit_code == 0, result.output
    [detail] = json.loads(result.output)
    assert detail["format"] == fmt
    payload = LUMINA_PAYLOAD if fmt == "lumina" else FLIRT_PAYLOAD
    assert detail["sha256"] == sha256_hex(payload)
    assert _release_reads(fake_repo) == {f"{fmt}/release"}

    human = _invoke(fmt, "show", "msvc/windows-all-x86").output
    assert human.startswith(f"{fmt} msvc/windows-all-x86  r2")


@pytest.mark.parametrize(("fmt", "content"), [("lumina", "42 funcs"), ("flirt", "3 sigs")])
def test_list_reads_only_its_groups_release(fake_repo: FakeBackend, fmt: str, content: str) -> None:
    output = json.loads(_invoke(fmt, "list", "--json").output)
    assert list(output["releases"]) == [fmt]
    assert [(entry["format"], entry["address"]) for entry in output["packs"]] == [(fmt, "msvc/windows-all-x86")]
    assert _release_reads(fake_repo) == {f"{fmt}/release"}

    human = _invoke(fmt, "list").output
    assert "FORMAT" not in human
    assert content in human


def test_flirt_list_ignores_the_lumina_server(fake_repo: FakeBackend) -> None:
    env = {SERVER_ENV: "http://127.0.0.1:18080"}
    output = json.loads(_invoke("flirt", "list", "--json", env=env).output)
    assert "lumina_server" not in output
    assert all("pushed" not in entry for entry in output["packs"])
    assert "pushed to" not in _invoke("flirt", "list", env=env).output

    lumina = json.loads(_invoke("lumina", "list", "--json", env=env).output)
    assert lumina["lumina_server"] == "http://127.0.0.1:18080"
    assert [entry["pushed"] for entry in lumina["packs"]] == [None]


def test_unknown_pack_hint_names_the_group(fake_repo: FakeBackend) -> None:
    result = _invoke("flirt", "show", "gcc/*", "--json")
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["error"].startswith("No flirt pack matches 'gcc/*'")
    assert error["hint"] == "pick an address from `hcli packs flirt list`"


@pytest.mark.parametrize(
    "args",
    [
        ["lumina", "list"],
        ["flirt", "list"],
        ["lumina", "show", "msvc/windows-all-x86"],
        ["flirt", "show", "msvc/windows-all-x86"],
        ["lumina", "get", "msvc/windows-all-x86"],
        ["flirt", "get", "msvc/windows-all-x86"],
    ],
)
def test_format_option_is_gone(fake_repo: FakeBackend, args: list[str]) -> None:
    result = _invoke(*args, "--format", "flirt")
    assert result.exit_code == 2
    assert "No such option: --format" in result.output
    assert fake_repo.reads == []


@pytest.mark.parametrize(
    "args",
    [["list"], ["show", "msvc/windows-all-x86"], ["get", "msvc/windows-all-x86"], ["push", "x"], ["sync"]],
)
def test_no_top_level_pack_commands(fake_repo: FakeBackend, args: list[str]) -> None:
    result = _invoke(*args)
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_flirt_has_no_push_command() -> None:
    result = _invoke("flirt", "push")
    assert result.exit_code == 2
    assert "No such command" in result.output


def _pack(**overrides: Any) -> ReleasePack:
    return ReleasePack.model_validate(make_pack("x86", b"p", b"m", **overrides))


def test_output_targets_reject_two_packs_on_one_file(tmp_path: Path) -> None:
    first = _pack()
    second = _pack(platform="linux")  # same component and published filename, different address
    with pytest.raises(InvalidReleaseError, match="would both be written to"):
        output_targets(tmp_path, PackFormat.LUMINA, [first, second])


def test_output_targets_collide_case_insensitively(tmp_path: Path) -> None:
    lower = _pack()
    upper = _pack(platform="linux", path="packs/msvc/WINDOWS-all-x86-r2.tar.zst")
    with pytest.raises(InvalidReleaseError, match="would both be written to"):
        output_targets(tmp_path, PackFormat.LUMINA, [lower, upper])


def test_output_targets_refuse_to_escape_the_output_folder(tmp_path: Path) -> None:
    # model_copy(update=) skips validation: the models already reject such a path,
    # and this is the second line of defense in get itself.
    escaping = _pack().model_copy(update={"component": "../../.."})
    with pytest.raises(InvalidReleaseError, match="outside the output folder"):
        output_targets(tmp_path / "out", PackFormat.LUMINA, [escaping])


def test_output_targets_layout(tmp_path: Path) -> None:
    [(_, target)] = output_targets(tmp_path, PackFormat.FLIRT, [_pack()])
    assert target == tmp_path / "flirt" / "msvc" / "windows-all-x86-r2.tar.zst"


# errors at the CLI boundary


def _refused(monkeypatch: pytest.MonkeyPatch, cache_root: Path) -> FakeBackend:
    """The portal refuses hcli's credentials (401) on the first read."""
    error = CredentialsError("Not logged in to the portal (https://portal.test).", "log in with `hcli login`")
    backend = FakeBackend(_objects(), {"read": error})
    use_backend(monkeypatch, backend)
    return backend


def test_json_error_object_and_exit_code(monkeypatch: pytest.MonkeyPatch, cache_root: Path) -> None:
    backend = _refused(monkeypatch, cache_root)
    result = _invoke("lumina", "list", "--json")
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "error": "Not logged in to the portal (https://portal.test).",
        "kind": "credentials",
        "hint": "log in with `hcli login`",
    }
    assert backend.reads == []
    assert backend.closed


def test_human_error_has_probably_fixed_by(monkeypatch: pytest.MonkeyPatch, cache_root: Path) -> None:
    _refused(monkeypatch, cache_root)
    result = _invoke("flirt", "list")
    assert result.exit_code == 1
    assert "Not logged in to the portal" in result.output
    assert "Probably fixed by: log in with `hcli login`" in result.output


PORTAL_COMMANDS: Final = [
    ["lumina", "list"],
    ["lumina", "show", "msvc/windows-all-x86"],
    ["lumina", "get", "msvc/windows-all-x86"],
    ["lumina", "push", "msvc/windows-all-x86", "--server", "https://lumina.test"],
    ["lumina", "sync", "--server", "https://lumina.test"],
    ["flirt", "list"],
    ["flirt", "show", "msvc/windows-all-x86"],
    ["flirt", "get", "msvc/windows-all-x86"],
    ["flirt", "install", "msvc/windows-all-x86"],
    ["flirt", "sync"],
]


@pytest.mark.parametrize("args", PORTAL_COMMANDS, ids=" ".join)
@pytest.mark.parametrize(("expired", "words"), [(False, "not logged in"), (True, "has expired")])
def test_logged_out_portal_commands_never_open_the_repository(
    monkeypatch: pytest.MonkeyPatch, cache_root: Path, args: list[str], expired: bool, words: str
) -> None:
    log_out(monkeypatch, expired=expired)
    backend = FakeBackend(_objects())
    use_backend(monkeypatch, backend)
    # --json changes nothing: hcli's login check runs before the command and prints text.
    result = _invoke(*args, "--json")
    assert result.exit_code == 1
    assert words in result.output and "login" in result.output
    assert backend.reads == []
    assert not backend.closed  # Never opened.


def test_doctor_runs_when_logged_out(monkeypatch: pytest.MonkeyPatch, no_tcp: None) -> None:
    # doctor reports a missing login instead of being refused for it.
    log_out(monkeypatch)
    use_backend(monkeypatch, FakeBackend(_objects()))
    result = _invoke("doctor", "--json")
    assert result.exit_code == 1
    checks = {check["name"]: check for check in json.loads(result.output)["checks"]}
    assert checks["credentials"]["status"] == "fail"


def test_unexpected_exception_is_wrapped(fake_repo: FakeBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("hcli.commands.packs.list.cache_dir", boom)
    result = _invoke("lumina", "list", "--json")
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "error": "Unexpected error: RuntimeError: boom",
        "kind": "repository_error",
        "hint": "re-run with HCLI_DEBUG=1 for the traceback, and report it",
    }
    assert fake_repo.closed


# doctor


@pytest.fixture
def no_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """The network check's TCP connect succeeds without touching the network."""
    monkeypatch.setattr(
        "hcli.lib.packs.repository.socket.create_connection", lambda *_a, **_k: contextlib.nullcontext()
    )


def _checks(*args: Any) -> list[Any]:
    """doctor's checks, run to completion."""
    return asyncio.run(_run_checks(*args))


def _statuses(checks: list[Any]) -> dict[str, Status]:
    return {check.name: check.status for check in checks}


def test_doctor_all_ok(monkeypatch: pytest.MonkeyPatch, no_tcp: None, tmp_path: Path) -> None:
    monkeypatch.setenv("HCLI_IDAUSR", str(tmp_path))
    (tmp_path / "sig").mkdir()
    monkeypatch.setenv(SERVER_ENV, "http://127.0.0.1:18080")
    monkeypatch.setenv(API_KEY_ENV, "k" * 40)
    checks = _checks(FakeBackend(_objects()), _lumina_transport())
    assert set(_statuses(checks).values()) == {Status.OK}
    by_name = {check.name: check for check in checks}
    assert "uploader" not in by_name  # pushes run in-process: nothing to install
    assert by_name["lumina api key"].detail == f"{API_KEY_ENV} accepted (read-only lookup)"


@pytest.mark.parametrize(
    ("expired", "detail", "hint"),
    [
        (False, "Not logged in to the portal.", "log in with `hcli login`, or set HCLI_API_KEY"),
        (True, "Your portal session has expired.", "log in again with `hcli login`"),
    ],
)
def test_doctor_skips_reads_when_logged_out(
    monkeypatch: pytest.MonkeyPatch, no_tcp: None, expired: bool, detail: str, hint: str
) -> None:
    log_out(monkeypatch, expired=expired)
    backend = FakeBackend(_objects())
    checks = _checks(backend)
    credentials = next(check for check in checks if check.name == "credentials")
    assert (credentials.detail, credentials.hint, credentials.kind) == (detail, hint, "credentials")
    assert _statuses(checks) == {
        "portal": Status.OK,
        "credentials": Status.FAIL,
        "network": Status.OK,
        "lumina release": Status.SKIP,
        "flirt release": Status.SKIP,
        "flirt sig dir": Status.WARN,  # The IDA user folder has no sig/ yet: install creates it.
        "lumina server": Status.SKIP,
        "lumina api key": Status.SKIP,
    }
    assert backend.reads == []
    assert backend.closed


def test_doctor_warns_for_a_format_not_published_yet(monkeypatch: pytest.MonkeyPatch, no_tcp: None) -> None:
    # The portal has no FLIRT Release yet: a warning, the Lumina one still checks out.
    objects = _objects()
    del objects["flirt/release"]
    by_name = {check.name: check for check in _checks(FakeBackend(objects))}
    assert by_name["lumina release"].status is Status.OK
    assert by_name["flirt release"].status is Status.WARN
    assert by_name["flirt release"].kind == "release_unavailable"


def test_doctor_fails_when_the_release_is_missing_from_storage(monkeypatch: pytest.MonkeyPatch, no_tcp: None) -> None:
    # Not a format that is not published yet: the portal lists it, the storage lost it.
    checks = _checks(FakeBackend(_objects(), {"read": RequestRefused(404, "storage")}))
    by_name = {check.name: check for check in checks}
    assert by_name["lumina release"].status is Status.FAIL
    assert by_name["lumina release"].kind == "not_found"


def test_doctor_fails_a_release_the_portal_refuses(monkeypatch: pytest.MonkeyPatch, no_tcp: None) -> None:
    checks = _checks(FakeBackend(_objects(), {"read": RequestRefused(403, "portal")}))
    by_name = {check.name: check for check in checks}
    assert by_name["lumina release"].status is Status.FAIL
    assert by_name["lumina release"].kind == "access_denied"


def _lumina_transport(*, ready: bool = True, key_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health/ready":
            return httpx.Response(200 if ready else 503, json={"ready": ready, "db": "up"})
        if request.url.path == "/api/metadata/lookup":
            assert request.headers["Authorization"] == "Bearer " + "k" * 40
            assert json.loads(request.content) == {"patterns": ["00112233445566778899aabbccddeeff"]}
            if key_status != 200:
                return httpx.Response(key_status, json={"error": "unauthorized", "message": "invalid api key"})
            return httpx.Response(200, json={"results": [{"code": "NOT_FOUND"}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_doctor_server_refuses_key(monkeypatch: pytest.MonkeyPatch, no_tcp: None) -> None:
    monkeypatch.setenv(SERVER_ENV, "http://127.0.0.1:18080/")
    monkeypatch.setenv(API_KEY_ENV, "k" * 40)
    checks = _checks(FakeBackend(_objects()), _lumina_transport(key_status=401))
    by_name = {check.name: check for check in checks}
    assert by_name["lumina server"].status is Status.OK
    assert by_name["lumina api key"].status is Status.FAIL
    assert by_name["lumina api key"].kind == "access_denied"
    assert "k" * 40 not in json.dumps([check.detail for check in checks])


def test_doctor_server_not_ready_skips_key(monkeypatch: pytest.MonkeyPatch, no_tcp: None) -> None:
    monkeypatch.setenv(SERVER_ENV, "http://127.0.0.1:18080")
    monkeypatch.setenv(API_KEY_ENV, "k" * 40)
    checks = _checks(FakeBackend(_objects()), _lumina_transport(ready=False))
    assert _statuses(checks)["lumina server"] is Status.FAIL
    assert _statuses(checks)["lumina api key"] is Status.SKIP


def test_doctor_cli_json_ok_with_warnings(monkeypatch: pytest.MonkeyPatch, no_tcp: None) -> None:
    objects = _objects()
    del objects["flirt/release"]
    backend = FakeBackend(objects)
    # The package re-exports the ``doctor`` command under the module's name.
    doctor_module = importlib.import_module("hcli.commands.packs.doctor")
    monkeypatch.setattr(doctor_module, "PackRepository", lambda _backend=None: PackRepository(backend))
    result = _invoke("doctor", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["ok"] is True
    # No FLIRT Release yet: a warning, not a failure.
    assert {check["name"]: check["status"] for check in report["checks"]}["flirt release"] == "warn"


# rendering helpers


@pytest.mark.parametrize(
    ("fmt", "overrides", "expected"),
    [
        (PackFormat.LUMINA, {"rows": 1234567}, "1,234,567 funcs"),
        (PackFormat.LUMINA, {"rows": None, "members": 12}, "12 members"),
        (PackFormat.FLIRT, {"rows": None, "members": 3456}, "3,456 sigs"),
    ],
)
def test_format_content(fmt: PackFormat, overrides: dict[str, Any], expected: str) -> None:
    assert format_content(fmt, _pack(**overrides)) == expected


def test_version_key_sorts_numerically_with_preview_last() -> None:
    values = ["14.4", "Preview", "14.29", "14.10", "9.5"]
    assert sorted(values, key=_version_key) == ["9.5", "14.4", "14.10", "14.29", "Preview"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (META_RECORD["name"], "14.29.30133/atlmfc/lib/arm64/atls.lib"),
        ("other/lib/foo.lib", "other/lib/foo.lib"),
    ],
)
def test_short_library_name(name: str, expected: str) -> None:
    assert short_library_name(name) == expected


def test_doctor_uses_the_ca_bundle_env(monkeypatch: pytest.MonkeyPatch, no_tcp: None, tmp_path: Path) -> None:
    monkeypatch.setenv(SERVER_ENV, "https://lumina.example.com")
    monkeypatch.setenv(CA_CERT_ENV, str(tmp_path / "missing.pem"))
    checks = _checks(FakeBackend(_objects()), _lumina_transport())
    by_name = {check.name: check for check in checks}
    # The bundle is loaded for the probe: a missing file fails the server check.
    assert by_name["lumina server"].status is Status.FAIL
    assert by_name["lumina server"].kind == "local_io"
    assert str(tmp_path / "missing.pem") in by_name["lumina server"].detail
