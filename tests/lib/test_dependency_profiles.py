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

    assert optional_dependencies["interactive"] == ["click", "rich", "rich-click", "questionary"]
    assert optional_dependencies["auth"] == ["supabase", "gotrue>=2.12.0"]
    assert optional_dependencies["plugin"] == ["requests>=2.32.4", "pip>=25.2", "pyyaml>=6.0.2"]

    assert optional_dependencies["app"] == [
        "click",
        "rich",
        "rich-click",
        "questionary",
        "supabase",
        "gotrue>=2.12.0",
        "requests>=2.32.4",
        "pip>=25.2",
        "pyyaml>=6.0.2",
    ]
