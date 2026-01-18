from importlib.metadata import entry_points
from typing import Any, Callable

# Global extensions cache
_extensions_cache: list[dict[str, Any]] | None = None


def get_extensions() -> list[dict[str, Any]]:
    """Get cached extensions list, loading if necessary."""
    global _extensions_cache

    if _extensions_cache is None:
        _extensions_cache = load_extensions()

    return _extensions_cache


def load_extensions() -> list[dict[str, Any]]:
    """Load extensions from entry points with version information."""
    eps = entry_points()
    extensions: list[dict[str, Any]] = []

    for ep in eps.select(group="hcli.extensions"):
        extension_func: Callable[..., Any] = ep.load()

        # Try to get version from the module
        module: str = extension_func.__module__
        try:
            import importlib

            mod = importlib.import_module(module.split(".")[0])  # Get root module
            version = getattr(mod, "__version__", "unknown")
        except Exception:
            version = "unknown"

        extensions.append({"name": ep.name, "version": version, "function": extension_func})

    return extensions
