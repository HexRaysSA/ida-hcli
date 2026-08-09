# Environment Variables

You can tweak the behavior of HCLI using environment variables.
Typically you won't have to touch these, but they can be useful in automation contexts, such as providing authentication tokens or overriding output directories.


## Core Configuration

| Variable             | Default Value | Purpose                                                                    |
|----------------------|---------------|----------------------------------------------------------------------------|
| HCLI_API_KEY         | None          | API key for authentication with Hex-Rays services                          |
| HCLI_DISABLE_UPDATES | false         | Disable automatic update checking                                          |
| HCLI_LOG_LEVEL       | None          | Set logging level (e.g., "DEBUG", "INFO", "WARNING")                       |
| HCLI_DEBUG           | false         | Enable debug mode with verbose logging (accepts: "true", "yes", "on", "1") |


## Standard System Variables

HCLI respects the following system variables:

| Variable       | Purpose                                                          |
|----------------|------------------------------------------------------------------|
| XDG_CACHE_HOME | Linux XDG cache home directory                                   |
| APPDATA        | Windows application data directory                               |
| LOCALAPPDATA   | Windows local application data directory                         |
| ProgramFiles   | Windows program files directory                                  |
| HOME           | User home directory (Linux/macOS)                                |

Furthermore, HCLI reads the following IDA Pro-related environment variables:

| Variable                      | Purpose                                                          |
|-------------------------------|------------------------------------------------------------------|
| IDAUSR                        | Standard IDA Pro user directory (checked if HCLI_IDAUSR not set) |
| IDADIR                        | Set by HCLI during IDA Pro execution contexts                    |
| IDAPYTHON_VENV_EXECUTABLE     | Python interpreter for IDA's virtualenv; used for plugin dependency management when the file exists (lower priority than `HCLI_CURRENT_IDA_PYTHON_EXE`) |

## Network & API Endpoints

| Variable         | Default Value                    | Purpose                                                       |
|------------------|----------------------------------|---------------------------------------------------------------|
| HCLI_API_URL     | "https://api.eu.hex-rays.com"    | Base URL for Hex-Rays API                                     |
| HCLI_CLOUD_URL   | "https://api.hcli.run"           | Cloud services URL                                            |
| HCLI_PORTAL_URL  | "https://my.hex-rays.com"        | Portal/Dashboard URL for user authentication and file sharing |
| HCLI_RELEASE_URL | "https://hcli.docs.hex-rays.com" | URL for release documentation and version information         |

## GitHub Integration

HCLI interacts with GitHub during self-updates and reading from the plugin repository.
To avoid rate-limiting, particularly in automated settings, you can provide a `GITHUB_TOKEN` to authenticate your requests.

| Variable                     | Default Value                           | Purpose                                                       |
|------------------------------|-----------------------------------------|---------------------------------------------------------------|
| GITHUB_TOKEN and GH_TOKEN    | None                                    | GitHub API authentication token                               |
| GITHUB_API_URL               | "https://api.github.com"                | GitHub API endpoint                                           |
| HCLI_GITHUB_URL              | "https://github.com/HexRaysSA/ida-hcli" | HCLI repository URL on GitHub                                 |


## IDA Pro Configuration

The following envrionment variables can be used to override system/version/configuration detection.

| Variable                     | Base Value                                   | Purpose                                                       |
|------------------------------|----------------------------------------------|---------------------------------------------------------------|
| HCLI_IDAUSR                  | e.g., ~/.idapro/                             | Override IDA Pro user configuration directory path            |
| HCLI_CURRENT_IDA_INSTALL_DIR | e.g., C:/Program Files/IDA Professional 9.2/ | Override current IDA Pro installation directory               |
| HCLI_CURRENT_IDA_PLATFORM    | auto-detected                                | Override IDA platform (e.g., "macos-aarch64", "linux-x86_64") |
| HCLI_CURRENT_IDA_VERSION     | auto-detected                                | Override IDA version (e.g., "9.1", "9.2")                     |
| HCLI_CURRENT_IDA_PYTHON_EXE  | auto-detected                                | Override Python executable path for IDA Pro                   |

Native IDA installers register the active idalib installation in
`$IDAUSR/ida-config.json`. HCLI automatically imports that installation into
its multi-installation registry and selects it when the existing HCLI default
is also idalib-capable. This keeps plugin management aligned with a newly
installed IDA while preserving an intentionally selected GUI-only default.

You can select another registered installation with `hcli ida switch`. If no
installation is registered or configured, set `HCLI_CURRENT_IDA_INSTALL_DIR`
when using the plugin manager so HCLI can find IDA and its resources.


## Cache & Storage

| Variable       | Default Value      | Purpose                                                      |
|----------------|--------------------|--------------------------------------------------------------|
| HCLI_CACHE_DIR | Platform-specific* | Override default cache directory for plugins and cached data |

*Platform-specific defaults:
- Linux: $XDG_CACHE_HOME/hex-rays/hcli/ or ~/.cache/hex-rays/hcli/
- Windows: %LOCALAPPDATA%\hex-rays\hcli\cache
- macOS: ~/Library/Caches/hex-rays/hcli/
