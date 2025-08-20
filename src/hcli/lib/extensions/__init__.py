from importlib.metadata import entry_points

# Global extensions cache
_extensions_cache: list[dict] | None = None


def get_extensions():
    """Get cached extensions list, loading if necessary."""
    global _extensions_cache

    if _extensions_cache is None:
        _extensions_cache = load_extensions()

    return _extensions_cache


def load_extensions():
    """Load extensions from entry points with version information."""
    eps = entry_points()
    extensions = []

    for ep in eps.select(group="hcli.extensions"):
        extension_func = ep.load()

        # Try to get version from the module
        module = extension_func.__module__
        try:
            import importlib

            mod = importlib.import_module(module.split(".")[0])  # Get root module
            version = getattr(mod, "__version__", "unknown")
        except Exception:
            version = "unknown"

        extensions.append({"name": ep.name, "version": version, "function": extension_func})

    return extensions
