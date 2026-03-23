from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib_fallback

    tomllib = tomllib_fallback


REPO_ROOT = Path(__file__).resolve().parents[2]


def get_dependency_profiles() -> tuple[list[str], dict[str, list[str]]]:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    project = pyproject["project"]
    return project["dependencies"], project["optional-dependencies"]


def test_core_dependencies_exclude_app_only_packages():
    dependencies, _ = get_dependency_profiles()

    assert "supabase" not in dependencies
    assert "questionary" not in dependencies
    assert "rich-click" not in dependencies


def test_app_profile_contains_cli_and_auth_dependencies():
    _, optional_dependencies = get_dependency_profiles()

    interactive = set(optional_dependencies["interactive"])
    auth = set(optional_dependencies["auth"])
    plugin = set(optional_dependencies["plugin"])
    app = set(optional_dependencies["app"])

    assert {"click", "rich", "rich-click", "questionary"} <= interactive
    assert {"supabase", "gotrue>=2.12.0"} <= auth
    assert {"requests>=2.32.4", "pip>=25.2", "pyyaml>=6.0.2"} <= plugin

    assert interactive <= app
    assert auth <= app
    assert plugin <= app
