from pathlib import Path

_THIS_FILE = Path(__file__)
TESTS_DIR = _THIS_FILE.parent.parent
PLUGINS_DIR = TESTS_DIR / "data" / "plugins"
PROJECT_DIR = TESTS_DIR.parent
