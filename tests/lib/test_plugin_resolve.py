"""Tests for the plugin resolver in `hcli.lib.ida.plugin.resolve`."""

from __future__ import annotations

import pytest

from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveLocation
from hcli.lib.ida.plugin.resolve import (
    AmbiguousRequirementError,
    Cell,
    Requirement,
    ResolutionError,
    StepLimitError,
    get_requirements,
    resolve,
)

HOST = "https://github.com/test/test"
OTHER_HOST = "https://github.com/other/other"
LINUX = "linux-x86_64"
WINDOWS = "windows-x86_64"
ALL = [LINUX, WINDOWS, "macos-x86_64", "macos-aarch64"]


def make_metadata_dict(
    name: str,
    version: str,
    *,
    host: str = HOST,
    platforms: list[str] | None = None,
    ida_versions: list[str] | None = None,
    requires_python: str | None = None,
    deps: list | None = None,
    components: list | None = None,
) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": host},
        "authors": [{"name": "Test", "email": "test@example.com"}],
        "platforms": platforms or ALL,
    }
    if ida_versions is not None:
        plugin["idaVersions"] = ida_versions
    if requires_python is not None:
        plugin["requiresPython"] = requires_python
    if deps is not None:
        plugin["dependencies"] = deps
    if components is not None:
        plugin["components"] = components
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def loc(name: str, version: str, **kwargs) -> PluginArchiveLocation:
    host = kwargs.get("host", HOST)
    platforms = kwargs.get("platforms") or ALL
    return PluginArchiveLocation(
        url=f"file:///{name}-{version}-{'+'.join(platforms)}-{host.rsplit('/', 1)[-1]}.zip",
        sha256="0" * 64,
        metadata=IDAMetadataDescriptor.model_validate(make_metadata_dict(name, version, **kwargs)),
    )


class ListPluginRepo(BasePluginRepo):
    """A repository built from locations, counting `get_plugins` calls."""

    def __init__(self, *locations: PluginArchiveLocation) -> None:
        plugins: dict[tuple[str, str], Plugin] = {}
        for location in locations:
            meta = location.metadata.plugin
            plugin = plugins.setdefault(
                (meta.name.lower(), meta.host), Plugin(name=meta.name, host=meta.host, versions={})
            )
            plugin.versions.setdefault(meta.version, []).append(location)
        self.plugins = list(plugins.values())
        self.calls = 0

    def get_plugins(self) -> list[Plugin]:
        self.calls += 1
        return self.plugins


def req(spec: str, required: bool = True) -> Requirement:
    return Requirement.from_spec(spec, required=required)


def versions(resolution) -> dict[str, str]:
    return {name: location.metadata.plugin.version for name, location in resolution.selected.items()}


def test_example_a_root_backtracks_to_older_version():
    repo = ListPluginRepo(
        loc("a", "2.0.0", deps=["b"]),
        loc("a", "1.0.0"),
        loc("b", "1.0.0", platforms=[LINUX]),
    )

    assert versions(resolve([req("a")], repo, Cell(LINUX))) == {"a": "2.0.0", "b": "1.0.0"}
    assert versions(resolve([req("a")], repo, Cell(WINDOWS))) == {"a": "1.0.0"}


def test_example_b_dependency_backtracks_to_older_version():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b"]),
        loc("b", "2.0.0", deps=["c"]),
        loc("b", "1.0.0"),
        loc("c", "1.0.0", platforms=[LINUX]),
    )

    assert versions(resolve([req("a")], repo, Cell(LINUX))) == {"a": "1.0.0", "b": "2.0.0", "c": "1.0.0"}
    assert versions(resolve([req("a")], repo, Cell(WINDOWS))) == {"a": "1.0.0", "b": "1.0.0"}


def test_example_c_requires_python_selects_per_python_version():
    repo = ListPluginRepo(
        loc("a", "2.0.0", requires_python=">=3.12"),
        loc("a", "1.9.0"),
    )

    assert versions(resolve([req("a")], repo, Cell(LINUX, python_version="3.10"))) == {"a": "1.9.0"}
    assert versions(resolve([req("a")], repo, Cell(LINUX, python_version="3.12"))) == {"a": "2.0.0"}


def test_requires_python_failure_names_the_specifier_and_the_cell():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b"]), loc("b", "1.0.0", requires_python=">=3.12"))
    cell = Cell(LINUX, python_version="3.10", label="linux-x86_64-cp310")

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, cell)

    message = str(excinfo.value)
    assert excinfo.value.chain == ("a",)
    assert "a 1.0.0 needs b: b 1.0.0 requires Python >=3.12, and linux-x86_64-cp310 has Python 3.10" in message


def test_requires_python_is_tested_against_the_zero_patch_release():
    repo = ListPluginRepo(loc("a", "2.0.0", requires_python=">=3.12.1"), loc("a", "1.0.0"))

    assert versions(resolve([req("a")], repo, Cell(LINUX, python_version="3.12"))) == {"a": "1.0.0"}


def test_ida_version_filters_candidates():
    repo = ListPluginRepo(loc("a", "2.0.0", ida_versions=["9.2"]), loc("a", "1.0.0", ida_versions=["9.1"]))

    assert versions(resolve([req("a")], repo, Cell(LINUX, ida_version="9.1"))) == {"a": "1.0.0"}
    assert versions(resolve([req("a")], repo, Cell(LINUX))) == {"a": "2.0.0"}


def test_python_version_function_is_not_called_without_requires_python():
    calls: list[int] = []

    def get_python_version() -> str:
        calls.append(1)
        raise RuntimeError("probe failed")

    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b"]), loc("b", "1.0.0"))

    assert versions(resolve([req("a")], repo, Cell(LINUX, python_version=get_python_version))) == {
        "a": "1.0.0",
        "b": "1.0.0",
    }
    assert calls == []


def test_python_version_function_is_called_once():
    calls: list[int] = []

    def get_python_version() -> str:
        calls.append(1)
        return "3.12"

    repo = ListPluginRepo(
        loc("a", "2.0.0", requires_python=">=3.13", deps=["b"]),
        loc("a", "1.0.0", requires_python=">=3.10", deps=["b"]),
        loc("b", "1.0.0", requires_python=">=3.10"),
    )

    assert versions(resolve([req("a")], repo, Cell(LINUX, python_version=get_python_version))) == {
        "a": "1.0.0",
        "b": "1.0.0",
    }
    assert calls == [1]


def test_failed_python_probe_excludes_only_versions_that_declare_requires_python():
    calls: list[int] = []

    def get_python_version() -> str:
        calls.append(1)
        raise RuntimeError("no idat")

    repo = ListPluginRepo(
        loc("a", "2.0.0", deps=["b"]),
        loc("a", "1.0.0", requires_python=">=3.0"),
        loc("b", "1.0.0"),
        loc("b", "0.9.0", requires_python=">=3.0"),
    )

    assert versions(resolve([req("a")], repo, Cell(LINUX, python_version=get_python_version))) == {
        "a": "2.0.0",
        "b": "1.0.0",
    }
    assert calls == [1]


def test_failed_python_probe_is_the_reason_when_every_version_declares_requires_python():
    def get_python_version() -> str:
        raise RuntimeError("no idat")

    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b"]), loc("b", "1.0.0", requires_python=">=3.0"))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, Cell(LINUX, python_version=get_python_version))

    assert "a 1.0.0 needs b: b 1.0.0 requires Python >=3.0, and IDA's Python cannot be detected: no idat" in str(
        excinfo.value
    )


def test_get_plugins_is_called_once_per_resolution():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b", "c"]),
        loc("b", "1.0.0", deps=["c"]),
        loc("c", "1.0.0"),
    )

    resolve([req("a"), req("b")], repo, Cell(LINUX))

    assert repo.calls == 1


def test_pins_from_two_roots_agree_on_a_common_version():
    repo = ListPluginRepo(
        loc("r1", "1.0.0", deps=["b"]),
        loc("r2", "2.0.0", deps=["b==1.0.0"]),
        loc("b", "2.0.0"),
        loc("b", "1.0.0"),
    )

    assert versions(resolve([req("r1"), req("r2")], repo, Cell(LINUX))) == {
        "r1": "1.0.0",
        "r2": "2.0.0",
        "b": "1.0.0",
    }


def test_conflicting_pins_backtrack_to_a_root_version_that_agrees():
    repo = ListPluginRepo(
        loc("r1", "1.0.0", deps=["b==1.0.0"]),
        loc("r2", "2.0.0", deps=["b==2.0.0"]),
        loc("r2", "1.0.0", deps=["b==1.0.0"]),
        loc("b", "2.0.0"),
        loc("b", "1.0.0"),
    )

    assert versions(resolve([req("r1"), req("r2")], repo, Cell(LINUX))) == {
        "r1": "1.0.0",
        "r2": "1.0.0",
        "b": "1.0.0",
    }


def test_conflicting_pins_without_a_common_version_fail():
    repo = ListPluginRepo(
        loc("r1", "1.0.0", deps=["b==1.0.0"]),
        loc("r2", "1.0.0", deps=["b==2.0.0"]),
        loc("b", "2.0.0"),
        loc("b", "1.0.0"),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("r1"), req("r2")], repo, Cell(LINUX))

    message = str(excinfo.value)
    assert LINUX in message
    assert "r2 1.0.0 -> b==2.0.0" in message
    assert "b 1.0.0 is already selected" in message
    assert "r1 1.0.0" in message
    assert excinfo.value.cell == Cell(LINUX)
    assert excinfo.value.chain == ("r2 1.0.0", "b==2.0.0")


def test_pin_to_a_version_that_is_not_viable_fails():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b==2.0.0"]),
        loc("b", "2.0.0", deps=["c"]),
        loc("b", "1.0.0"),
        loc("c", "1.0.0", platforms=[LINUX]),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, Cell(WINDOWS))

    message = str(excinfo.value)
    assert WINDOWS in message
    assert "a" in excinfo.value.chain[0]
    assert "a 1.0.0 needs b==2.0.0" in message
    assert "b 2.0.0 needs c" in message
    assert f"c 1.0.0 does not support {WINDOWS}" in message


def test_missing_required_dependency_names_chain_and_reason():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b"]))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, Cell(WINDOWS))

    message = str(excinfo.value)
    assert WINDOWS in message
    assert excinfo.value.chain == ("a",)
    assert "a 1.0.0 needs b, which is not in the repository" in message


def test_failure_of_a_dependency_of_a_fixed_plugin_names_its_requirements():
    local = IDAMetadataDescriptor.model_validate(make_metadata_dict("local", "0.1.0", deps=["b"]))
    repo = ListPluginRepo(loc("b", "1.0.0", deps=["c==1.0.0"]), loc("c", "2.0.0"))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([], repo, Cell(LINUX), fixed={"local": local})

    assert excinfo.value.chain == ("local 0.1.0", "b")
    assert excinfo.value.requirements == (req("b"),)


def test_failure_below_a_root_names_every_requirement_on_the_way():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b"]),
        loc("b", "1.0.0", deps=["c==1.0.0"]),
        loc("c", "2.0.0"),
        loc("c", "1.0.0"),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("c==2.0.0"), req("a")], repo, Cell(LINUX))

    assert excinfo.value.chain == ("a 1.0.0", "b 1.0.0", "c==1.0.0")
    assert excinfo.value.requirements == (req("a"), req("b"), req("c==1.0.0"))


def test_missing_root_fails():
    repo = ListPluginRepo(loc("a", "1.0.0"))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("missing")], repo, Cell(LINUX))

    message = str(excinfo.value)
    assert LINUX in message
    assert excinfo.value.chain == ("missing",)
    assert "missing is not in the repository" in message


def test_root_with_no_version_matching_the_spec_fails():
    repo = ListPluginRepo(loc("a", "1.0.0"))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a>=2.0")], repo, Cell(LINUX))

    message = str(excinfo.value)
    assert LINUX in message
    assert excinfo.value.chain == ("a>=2.0",)
    assert "no a version matches >=2.0" in message


def test_cycle_of_required_dependencies_resolves():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b"]),
        loc("b", "1.0.0", deps=["a"]),
    )

    resolution = resolve([req("a")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0", "b": "1.0.0"}
    assert resolution.order == ["b", "a"]
    assert resolve([req("b")], repo, Cell(LINUX)).order == ["a", "b"]


def test_cycle_that_cannot_install_on_the_cell_is_removed():
    repo = ListPluginRepo(
        loc("a", "2.0.0", deps=["b"]),
        loc("a", "1.0.0"),
        loc("b", "1.0.0", deps=["a==2.0.0", "c"]),
        loc("c", "1.0.0", platforms=[LINUX]),
    )

    assert versions(resolve([req("a")], repo, Cell(LINUX))) == {"a": "2.0.0", "b": "1.0.0", "c": "1.0.0"}
    assert versions(resolve([req("a")], repo, Cell(WINDOWS))) == {"a": "1.0.0"}


def test_optional_dependency_with_failing_subtree_is_skipped():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[{"plugin": "opt", "required": False}, "b"]),
        loc("opt", "1.0.0", deps=["missing"]),
        loc("b", "1.0.0"),
    )

    resolution = resolve([req("a")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0", "b": "1.0.0"}
    assert len(resolution.skipped) == 1
    skipped = resolution.skipped[0]
    assert skipped.requirement.name == "opt"
    assert skipped.chain == ("a 1.0.0", "opt")
    assert "opt 1.0.0 needs missing, which is not in the repository" in skipped.reason


def test_optional_dependency_that_is_missing_is_skipped():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=[{"plugin": "opt", "required": False}]))

    resolution = resolve([req("a")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0"}
    assert [s.requirement.name for s in resolution.skipped] == ["opt"]
    assert "opt is not in the repository" in resolution.skipped[0].reason


def test_optional_dependency_is_skipped_when_it_conflicts_with_a_selected_version():
    repo = ListPluginRepo(
        loc("r1", "1.0.0", deps=["b==1.0.0"]),
        loc("r2", "1.0.0", deps=[{"plugin": "b==2.0.0", "required": False}]),
        loc("b", "2.0.0"),
        loc("b", "1.0.0"),
    )

    resolution = resolve([req("r1"), req("r2")], repo, Cell(LINUX))

    assert versions(resolution) == {"r1": "1.0.0", "r2": "1.0.0", "b": "1.0.0"}
    assert [s.requirement.name for s in resolution.skipped] == ["b"]
    assert "b 1.0.0 is already selected" in resolution.skipped[0].reason


def test_optional_dependency_does_not_affect_viability():
    repo = ListPluginRepo(
        loc("a", "2.0.0", deps=[{"plugin": "b", "required": False}]),
        loc("a", "1.0.0"),
        loc("b", "1.0.0", platforms=[LINUX]),
    )

    resolution = resolve([req("a")], repo, Cell(WINDOWS))

    assert versions(resolution) == {"a": "2.0.0"}
    assert [s.requirement.name for s in resolution.skipped] == ["b"]


def test_step_limit_gives_its_own_error():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[f"b{i}" for i in range(5)]),
        *(loc(f"b{i}", "2.0.0", deps=["z==2.0.0"]) for i in range(5)),
        *(loc(f"b{i}", "1.0.0", deps=["z==1.0.0"]) for i in range(5)),
        loc("z", "2.0.0"),
        loc("z", "1.0.0"),
        loc("tail", "1.0.0", deps=["z==3.0.0"]),
    )

    with pytest.raises(StepLimitError) as excinfo:
        resolve([req("a"), req("tail")], repo, Cell(LINUX), max_steps=20)

    message = str(excinfo.value)
    assert "20 steps" in message
    assert excinfo.value.cell == Cell(LINUX)
    assert excinfo.value.chain
    assert f"cannot resolve {' -> '.join(excinfo.value.chain)} for {LINUX}" in message


def test_requirement_with_host_selects_that_plugin():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[f"b@{OTHER_HOST}"]),
        loc("b", "2.0.0"),
        loc("b", "1.0.0", host=OTHER_HOST),
    )

    resolution = resolve([req("a")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0", "b": "1.0.0"}
    assert resolution.selected["b"].metadata.plugin.host == OTHER_HOST


def test_root_with_host_selects_that_plugin():
    repo = ListPluginRepo(loc("a", "2.0.0"), loc("a", "1.0.0", host=OTHER_HOST))

    resolution = resolve([req("a@https://github.com/Other/Other")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0"}


def test_ambiguous_dependency_is_a_hard_error():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[{"plugin": "b", "required": False}]),
        loc("b", "1.0.0"),
        loc("b", "1.0.0", host=OTHER_HOST),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, Cell(LINUX))

    message = str(excinfo.value)
    assert LINUX in message
    assert excinfo.value.chain == ("a 1.0.0", "b")
    assert "b is ambiguous" in message
    assert HOST in message
    assert OTHER_HOST in message


def test_ambiguous_root_is_a_hard_error():
    repo = ListPluginRepo(loc("a", "1.0.0"), loc("a", "1.0.0", host=OTHER_HOST))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, Cell(LINUX))

    assert excinfo.value.chain == ("a",)
    assert excinfo.value.cell == Cell(LINUX)
    assert str(excinfo.value).startswith(f"cannot resolve a for {LINUX}: a is ambiguous")


def test_names_match_case_insensitively():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["plugin-b"]), loc("Plugin-B", "1.0.0"))

    resolution = resolve([req("A")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0", "Plugin-B": "1.0.0"}
    assert resolution.roots == {req("A"): "a"}


def test_component_dependencies_are_requirements():
    component = make_metadata_dict("suite-part", "1.0.0", deps=["b"])
    suite = loc("suite", "2.0.0", components=[component])

    assert [r.name for r in get_requirements(suite.metadata)] == ["b"]

    repo = ListPluginRepo(suite, loc("suite", "1.0.0"), loc("b", "1.0.0", platforms=[LINUX]))

    assert versions(resolve([req("suite")], repo, Cell(LINUX))) == {"suite": "2.0.0", "b": "1.0.0"}
    assert versions(resolve([req("suite")], repo, Cell(WINDOWS))) == {"suite": "1.0.0"}


def test_nested_component_dependencies_are_requirements():
    inner = make_metadata_dict("inner", "1.0.0", deps=["c==1.0.0"])
    outer = make_metadata_dict("outer", "1.0.0", deps=[{"plugin": "b", "required": False}], components=[inner])
    suite = loc("suite", "1.0.0", deps=["a"], components=[outer, "unexpanded"])

    assert [(r.name, r.version_spec, r.required) for r in get_requirements(suite.metadata)] == [
        ("a", "", True),
        ("b", "", False),
        ("c", "==1.0.0", True),
    ]


def test_installed_plugin_satisfies_bare_requirement():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b"]), loc("b", "2.0.0", deps=["missing"]))

    resolution = resolve([req("a")], repo, Cell(LINUX), installed={"b": "1.0.0"})

    assert versions(resolution) == {"a": "1.0.0"}
    assert resolution.order == ["a"]


def test_installed_plugin_satisfies_bare_requirement_that_is_missing_from_the_repository():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b"]))

    assert versions(resolve([req("a")], repo, Cell(WINDOWS), installed={"B": "1.0.0"})) == {"a": "1.0.0"}


def test_installed_plugin_at_pinned_version_needs_nothing():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b==1.0.0"]))

    resolution = resolve([req("a")], repo, Cell(LINUX), installed={"b": "1.0.0"})

    assert versions(resolution) == {"a": "1.0.0"}
    assert resolution.warnings == []


def test_installed_plugin_below_pin_is_upgraded():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b==2.0.0"]), loc("b", "2.0.0"), loc("b", "1.0.0"))

    resolution = resolve([req("a")], repo, Cell(LINUX), installed={"b": "1.0.0"})

    assert versions(resolution) == {"a": "1.0.0", "b": "2.0.0"}
    assert resolution.order == ["b", "a"]


def test_installed_plugin_above_pin_is_kept_with_a_warning():
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b==1.0.0"]), loc("b", "1.0.0"))

    resolution = resolve([req("a")], repo, Cell(LINUX), installed={"b": "2.0.0"})

    assert versions(resolution) == {"a": "1.0.0"}
    assert len(resolution.warnings) == 1
    assert "b 2.0.0" in resolution.warnings[0]
    assert "==1.0.0" in resolution.warnings[0]


def test_installed_plugin_below_pin_that_is_not_viable_fails_before_viability_of_parent():
    repo = ListPluginRepo(
        loc("a", "2.0.0", deps=["b==2.0.0"]),
        loc("a", "1.0.0", deps=["b"]),
        loc("b", "2.0.0", platforms=[LINUX]),
    )

    resolution = resolve([req("a")], repo, Cell(WINDOWS), installed={"b": "1.0.0"})

    assert versions(resolution) == {"a": "1.0.0"}


def test_bare_then_higher_pin_upgrades_installed_plugin():
    repo = ListPluginRepo(
        loc("r1", "1.0.0", deps=["b"]),
        loc("r2", "1.0.0", deps=["b==2.0.0"]),
        loc("b", "2.0.0"),
    )

    resolution = resolve([req("r1"), req("r2")], repo, Cell(LINUX), installed={"b": "1.0.0"})

    assert versions(resolution) == {"r1": "1.0.0", "r2": "1.0.0", "b": "2.0.0"}


def test_range_root_on_installed_plugin_selects_from_the_repository():
    repo = ListPluginRepo(loc("a", "2.0.0"), loc("a", "1.0.0"))

    root = Requirement("a", ">1.0.0", None)

    assert versions(resolve([root], repo, Cell(LINUX), installed={"a": "1.0.0"})) == {"a": "2.0.0"}


def test_fixed_root_requirements_are_resolved():
    local = IDAMetadataDescriptor.model_validate(make_metadata_dict("local", "0.1.0", deps=["b"]))
    repo = ListPluginRepo(loc("b", "2.0.0", platforms=[LINUX]), loc("b", "1.0.0"))

    resolution = resolve([], repo, Cell(WINDOWS), fixed={"local": local})

    assert versions(resolution) == {"b": "1.0.0"}
    assert resolution.order == ["b"]
    assert resolution.roots == {}


def test_root_that_names_a_fixed_plugin_maps_to_it():
    local = IDAMetadataDescriptor.model_validate(make_metadata_dict("local", "0.1.0", deps=["b"]))
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b==1.0.0"]), loc("b", "2.0.0"), loc("b", "1.0.0"))

    resolution = resolve([req("a"), req("local")], repo, Cell(LINUX), fixed={"local": local})

    assert versions(resolution) == {"a": "1.0.0", "b": "1.0.0"}
    assert resolution.roots == {req("a"): "a", req("local"): "local"}


def test_fixed_plugin_satisfies_a_requirement_on_its_version():
    local = IDAMetadataDescriptor.model_validate(make_metadata_dict("b", "0.1.0"))
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b"]), loc("b", "2.0.0"))

    resolution = resolve([req("a")], repo, Cell(LINUX), fixed={"b": local})

    assert versions(resolution) == {"a": "1.0.0"}


def test_fixed_plugin_that_does_not_match_a_pin_fails():
    local = IDAMetadataDescriptor.model_validate(make_metadata_dict("b", "0.1.0"))
    repo = ListPluginRepo(loc("a", "1.0.0", deps=["b==2.0.0"]), loc("b", "2.0.0"))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, Cell(LINUX), fixed={"b": local})

    message = str(excinfo.value)
    assert LINUX in message
    assert excinfo.value.chain == ("a",)
    assert "a 1.0.0 needs b==2.0.0" in message
    assert "b 0.1.0 is given" in message


def test_order_puts_dependencies_before_dependents():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b", "c"]),
        loc("b", "1.0.0", deps=["c"]),
        loc("c", "1.0.0"),
        loc("d", "1.0.0"),
    )

    resolution = resolve([req("a"), req("d")], repo, Cell(LINUX))

    assert resolution.order == ["c", "b", "a", "d"]
    assert resolution.roots == {req("a"): "a", req("d"): "d"}


def test_first_compatible_location_wins_within_a_version():
    linux = loc("a", "1.0.0", platforms=[LINUX])
    both = loc("a", "1.0.0", platforms=[LINUX, WINDOWS])
    repo = ListPluginRepo(linux, both)

    assert resolve([req("a")], repo, Cell(LINUX)).selected["a"] == linux
    assert resolve([req("a")], repo, Cell(WINDOWS)).selected["a"] == both


@pytest.mark.parametrize(
    "cell",
    [
        Cell(LINUX),
        Cell(WINDOWS),
        Cell(LINUX, ida_version="9.1"),
        Cell(WINDOWS, ida_version="9.2"),
    ],
)
@pytest.mark.parametrize("spec", ["a", "a==1.0.0", "a>=1.5", "a<=1.9", "b", f"b@{OTHER_HOST}", "c"])
def test_resolution_without_dependencies_matches_find_plugin_from_spec(cell: Cell, spec: str):
    repo = ListPluginRepo(
        loc("a", "2.0.0", platforms=[LINUX]),
        loc("a", "1.5.0", ida_versions=["9.2"]),
        loc("a", "1.0.0", platforms=[WINDOWS]),
        loc("a", "1.0.0", platforms=[LINUX]),
        loc("b", "3.0.0", host=OTHER_HOST, platforms=[WINDOWS]),
        loc("b", "2.0.0", host=OTHER_HOST),
        loc("c", "1.0.0", ida_versions=["9.1"]),
    )
    requirement = req(spec)

    try:
        expected = repo.find_plugin_from_spec(
            requirement.name + requirement.version_spec,
            cell.platform,
            cell.ida_version,
            host=requirement.host,
        )
    except KeyError:
        with pytest.raises(ResolutionError):
            resolve([requirement], repo, cell)
        return

    assert resolve([requirement], repo, cell).selected[expected.metadata.plugin.name] == expected


def test_requirement_from_spec_parses_host_and_range():
    assert Requirement.from_spec(f"a>=1.0@{OTHER_HOST}") == Requirement("a", ">=1.0", OTHER_HOST)
    assert str(Requirement("a", "==1.0.0", OTHER_HOST)) == f"a==1.0.0@{OTHER_HOST}"

    with pytest.raises(ValueError):
        Requirement.from_spec("a>=not-a-version")


def test_cell_label():
    assert str(Cell(LINUX)) == LINUX
    assert str(Cell(LINUX, python_version="3.12")) == f"{LINUX} Python 3.12"
    assert str(Cell(LINUX, ida_version="9.2", python_version=lambda: "3.12")) == f"{LINUX} IDA 9.2"
    assert str(Cell(LINUX, python_version="3.12", label="linux-x86_64-cp312")) == "linux-x86_64-cp312"


def test_optional_dependencies_do_not_multiply_steps_before_a_required_conflict():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[{"plugin": f"o{i}", "required": False} for i in range(12)]),
        *(loc(f"o{i}", "1.0.0") for i in range(12)),
        loc("r1", "1.0.0", deps=["q==1.0.0"]),
        loc("r2", "1.0.0", deps=["q==2.0.0"]),
        loc("q", "2.0.0"),
        loc("q", "1.0.0"),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a"), req("r1"), req("r2")], repo, Cell(LINUX))

    assert not isinstance(excinfo.value, StepLimitError)
    assert excinfo.value.chain == ("r2 1.0.0", "q==2.0.0")
    assert "q 1.0.0 is already selected for r1 1.0.0 -> q==1.0.0" in str(excinfo.value)


def test_optional_dependency_whose_subtree_conflicts_is_rolled_back():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[{"plugin": "x", "required": False}]),
        loc("x", "1.0.0", deps=["z", "y==2.0.0"]),
        loc("z", "1.0.0"),
        loc("r", "1.0.0", deps=["y==1.0.0"]),
        loc("y", "2.0.0"),
        loc("y", "1.0.0"),
    )

    resolution = resolve([req("a"), req("r")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0", "r": "1.0.0", "y": "1.0.0"}
    assert resolution.order == ["a", "y", "r"]
    assert resolution.warnings == []
    assert len(resolution.skipped) == 1
    skipped = resolution.skipped[0]
    assert skipped.chain == ("a 1.0.0", "x")
    assert "y 1.0.0 is already selected for r 1.0.0 -> y==1.0.0" in skipped.reason


def test_conflict_with_installed_plugin_names_every_constraint():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b==1.0.0"]),
        loc("c", "1.0.0", deps=["b==2.0.0"]),
        loc("b", "2.0.0"),
        loc("b", "1.0.0"),
    )
    cell = Cell(LINUX, label="linux-cell")

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a"), req("c")], repo, cell, installed={"b": "1.0.0"})

    message = str(excinfo.value)
    assert excinfo.value.chain == ("c 1.0.0", "b==2.0.0")
    assert "linux-cell" in message
    assert "b 1.0.0 is installed" in message
    assert "a 1.0.0 -> b==1.0.0" in message
    assert "c 1.0.0 -> b==2.0.0" in message


def test_conflicting_ranges_on_installed_plugin_name_every_constraint():
    repo = ListPluginRepo(loc("b", "2.0.0"), loc("b", "0.9.0"))

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("b>=1.0"), req("b<=1.2")], repo, Cell(LINUX), installed={"b": "2.0.0"})

    message = str(excinfo.value)
    assert "b 2.0.0 is installed" in message
    assert "b>=1.0" in message
    assert "b<=1.2" in message


@pytest.mark.parametrize("roots", [["a", "r"], ["r", "a"]])
def test_version_that_needs_an_ambiguous_plugin_is_an_error_in_any_root_order(roots: list[str]):
    repo = ListPluginRepo(
        loc("a", "2.0.0", deps=["x"]),
        loc("a", "1.0.0"),
        loc("r", "1.0.0", deps=["a==1.0.0"]),
        loc("x", "1.0.0"),
        loc("x", "1.0.0", host=OTHER_HOST),
    )

    with pytest.raises(AmbiguousRequirementError) as excinfo:
        resolve([req(root) for root in roots], repo, Cell(LINUX))

    assert excinfo.value.name == "x"
    assert excinfo.value.chain == ("a 2.0.0", "x")
    assert excinfo.value.cell == Cell(LINUX)
    assert excinfo.value.candidates == [("x", HOST), ("x", OTHER_HOST)]
    assert str(excinfo.value) == (
        f"cannot resolve a 2.0.0 -> x for {LINUX}: x is ambiguous, use one of: x@{HOST}, x@{OTHER_HOST}"
    )


def test_ambiguous_plugin_behind_a_version_that_cannot_install_is_an_error():
    repo = ListPluginRepo(
        loc("a", "2.0.0", deps=["missing", {"plugin": "x", "required": False}]),
        loc("a", "1.0.0"),
        loc("x", "1.0.0"),
        loc("x", "1.0.0", host=OTHER_HOST),
    )

    with pytest.raises(AmbiguousRequirementError) as excinfo:
        resolve([req("a")], repo, Cell(LINUX))

    assert excinfo.value.chain == ("a 2.0.0", "x")


def test_ambiguous_plugin_behind_a_location_for_another_platform_is_not_an_error():
    repo = ListPluginRepo(
        loc("a", "1.0.0", platforms=[WINDOWS], deps=["x"]),
        loc("a", "1.0.0", platforms=[LINUX]),
        loc("x", "1.0.0"),
        loc("x", "1.0.0", host=OTHER_HOST),
    )

    resolution = resolve([req("a")], repo, Cell(LINUX))

    assert versions(resolution) == {"a": "1.0.0"}


def test_ambiguous_name_satisfied_by_an_installed_plugin_is_not_an_error():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["x"]),
        loc("x", "1.0.0"),
        loc("x", "1.0.0", host=OTHER_HOST),
    )

    resolution = resolve([req("a")], repo, Cell(LINUX), installed={"x": "1.0.0"})

    assert versions(resolution) == {"a": "1.0.0"}


def test_ambiguous_name_that_needs_an_upgrade_of_an_installed_plugin_is_an_error():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["x==2.0.0"]),
        loc("x", "2.0.0"),
        loc("x", "2.0.0", host=OTHER_HOST),
    )

    with pytest.raises(AmbiguousRequirementError) as excinfo:
        resolve([req("a")], repo, Cell(LINUX), installed={"x": "1.0.0"})

    assert excinfo.value.chain == ("a 1.0.0", "x==2.0.0")


def test_ambiguous_name_of_a_fixed_plugin_is_not_an_error():
    fixed = IDAMetadataDescriptor.model_validate(make_metadata_dict("x", "1.0.0"))
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["x"]),
        loc("x", "1.0.0"),
        loc("x", "1.0.0", host=OTHER_HOST),
    )

    resolution = resolve([req("a")], repo, Cell(LINUX), fixed={"x": fixed})

    assert versions(resolution) == {"a": "1.0.0"}


def test_ambiguity_candidates_are_listed_once():
    repo = ListPluginRepo(loc("x", "1.0.0"), loc("x", "1.0.0", host=OTHER_HOST))
    repo.plugins.append(Plugin(name="x", host=HOST, versions={}))

    with pytest.raises(AmbiguousRequirementError) as excinfo:
        resolve([req("x")], repo, Cell(LINUX))

    assert excinfo.value.candidates == [("x", HOST), ("x", OTHER_HOST)]
    assert str(excinfo.value).endswith(f"use one of: x@{HOST}, x@{OTHER_HOST}")


def test_incompatible_version_is_explained_by_the_location_for_the_platform():
    repo = ListPluginRepo(
        loc("a", "1.0.0", platforms=[LINUX]),
        loc("a", "1.0.0", platforms=[WINDOWS], requires_python=">=3.12"),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a")], repo, Cell(WINDOWS, python_version="3.10"))

    message = str(excinfo.value)
    assert "a 1.0.0 requires Python >=3.12" in message
    assert "does not support" not in message


def test_dependency_chain_deeper_than_the_recursion_limit_gives_a_resolution_error():
    count = 1500
    repo = ListPluginRepo(
        *(loc(f"p{i}", "1.0.0", deps=[f"p{i + 1}"]) for i in range(count)),
        loc(f"p{count}", "1.0.0"),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("p0")], repo, Cell(LINUX))

    assert "too many" in str(excinfo.value)
    assert LINUX in str(excinfo.value)


def test_skipped_optional_requirement_is_reported_once():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[{"plugin": "x", "required": False}]),
        loc("b", "1.0.0", deps=[{"plugin": "x", "required": False}]),
    )

    resolution = resolve([req("a"), req("b")], repo, Cell(LINUX))

    assert [s.chain for s in resolution.skipped] == [("a 1.0.0", "x")]


def test_kept_newer_version_is_warned_once():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=["b==1.0.0"]),
        loc("c", "1.0.0", deps=["b==1.0.0"]),
        loc("b", "1.0.0"),
    )

    resolution = resolve([req("a"), req("c")], repo, Cell(LINUX), installed={"b": "2.0.0"})

    assert len(resolution.warnings) == 1


def test_requirement_with_another_host_conflicts_with_a_selected_plugin():
    repo = ListPluginRepo(
        loc("a", "1.0.0", deps=[f"b@{HOST}"]),
        loc("c", "1.0.0", deps=[f"b@{OTHER_HOST}"]),
        loc("b", "1.0.0"),
        loc("b", "1.0.0", host=OTHER_HOST),
    )

    with pytest.raises(ResolutionError) as excinfo:
        resolve([req("a"), req("c")], repo, Cell(LINUX))

    assert excinfo.value.chain == ("c 1.0.0", f"b@{OTHER_HOST}")
    assert f"b 1.0.0 is already selected for a 1.0.0 -> b@{HOST}" in str(excinfo.value)


@pytest.mark.parametrize("roots", [["r1", "r2"], ["r2", "r1"]])
def test_pin_below_installed_version_does_not_block_an_upgrade_in_any_root_order(roots: list[str]):
    repo = ListPluginRepo(
        loc("r1", "1.0.0", deps=["b==1.0.0"]),
        loc("r2", "1.0.0", deps=["b==3.0.0"]),
        loc("b", "3.0.0"),
        loc("b", "1.0.0"),
    )

    resolution = resolve([req(root) for root in roots], repo, Cell(LINUX), installed={"b": "2.0.0"})

    assert versions(resolution) == {"r1": "1.0.0", "r2": "1.0.0", "b": "3.0.0"}
    assert len(resolution.warnings) == 1


def test_skipped_optional_root_on_an_installed_plugin_is_not_a_root():
    repo = ListPluginRepo(loc("x", "3.0.0", platforms=[LINUX]))
    root = req("x>=3.0", required=False)

    resolution = resolve([root], repo, Cell(WINDOWS), installed={"x": "2.0.0"})

    assert resolution.roots == {}
    assert [item.requirement for item in resolution.skipped] == [root]
