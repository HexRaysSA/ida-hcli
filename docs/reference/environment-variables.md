# Environment Variables

You can tweak the behavior of HCLI using environment variables.
Typically you won't have to touch these, but they can be useful in automation contexts, such as providing authentication tokens or overriding output directories.


## Core Configuration

| Variable              | Default Value | Purpose                                                                    |
|-----------------------|---------------|----------------------------------------------------------------------------|
| HCLI_API_KEY          | None          | API key for authentication with Hex-Rays services                          |
| HCLI_DISABLE_UPDATES  | false         | Disable automatic update checking                                          |
| HCLI_DEBUG            | false         | Write debug log messages to stderr, print a traceback for unexpected errors, and show the output of pip when a package installation fails (accepts: "true", "yes", "on", "1") |


## Standard System Variables

HCLI respects the following system variables:

| Variable       | Purpose                                                          |
|----------------|------------------------------------------------------------------|
| XDG_CACHE_HOME | Linux XDG cache home directory                                   |
| APPDATA        | Windows application data directory                               |
| LOCALAPPDATA   | Windows local application data directory                         |
| ProgramFiles   | Windows program files directory                                  |
| HOME           | User home directory (Linux/macOS)                                |
| UV_CACHE_DIR   | uv cache directory. HCLI does not use a virtual environment in this directory as your virtual environment, because uv creates temporary environments there, for example for `uv run --with` |

Furthermore, HCLI reads the following IDA Pro-related environment variables:

| Variable                      | Purpose                                                          |
|-------------------------------|------------------------------------------------------------------|
| IDAUSR                        | Standard IDA Pro user directory (checked if HCLI_IDAUSR not set) |
| IDADIR                        | Set by HCLI during IDA Pro execution contexts                    |
| IDAPYTHON_VENV_EXECUTABLE     | Python interpreter for IDA's virtualenv. This is the recommended way to tell both IDA and HCLI which Python environment to use. `hcli ida python create-environment` sets it for you. See [IDA's Python Environment](../user-guide/ida-python-environment.md). Lower priority than `HCLI_CURRENT_IDA_PYTHON_EXE`. |

## Network & API Endpoints

| Variable         | Default Value                    | Purpose                                                       |
|------------------|----------------------------------|---------------------------------------------------------------|
| HCLI_API_URL     | "https://api.eu.hex-rays.com"    | Base URL for Hex-Rays API                                     |
| HCLI_CLOUD_URL   | "https://api.hcli.run"           | Cloud services URL                                            |
| HCLI_PORTAL_URL  | "https://my.hex-rays.com"        | Portal/Dashboard URL for user authentication and file sharing |
| HCLI_RELEASE_URL | "https://hcli.docs.hex-rays.com" | URL for release documentation and version information         |
| HCLI_SUPABASE_URL | "https://auth.hex-rays.com"     | Authentication service URL for `hcli login`                   |
| HCLI_SUPABASE_ANON_KEY | Built-in public key        | Public client key that HCLI sends to the authentication service |

### TLS Certificates

| Variable              | Default Value | Purpose                                                                                                         |
|-----------------------|---------------|-----------------------------------------------------------------------------------------------------------------|
| HCLI_USE_SYSTEM_CERTS | true          | Verify HTTPS certificates against the operating system certificate store. Set to "false" to use the bundled certifi store instead. |

HCLI uses the operating system certificate store by default, so a root certificate that your company installs for a TLS-inspecting proxy is trusted automatically.

## GitHub Integration

HCLI interacts with GitHub during self-updates and reading from the plugin repository.
To avoid rate-limiting, particularly in automated settings, you can provide a `GITHUB_TOKEN` to authenticate your requests.

| Variable                     | Default Value                           | Purpose                                                       |
|------------------------------|-----------------------------------------|---------------------------------------------------------------|
| GITHUB_TOKEN and GH_TOKEN    | None                                    | GitHub API authentication token                               |
| GITHUB_API_URL               | "https://api.github.com"                | GitHub API endpoint                                           |
| HCLI_GITHUB_URL              | "https://github.com/HexRaysSA/ida-hcli" | HCLI repository URL on GitHub                                 |


## IDA Pro Configuration

The following environment variables can be used to override system/version/configuration detection.

| Variable                     | Base Value                                   | Purpose                                                       |
|------------------------------|----------------------------------------------|---------------------------------------------------------------|
| HCLI_IDAUSR                  | e.g., ~/.idapro/                             | Override IDA Pro user configuration directory path            |
| HCLI_CURRENT_IDA_INSTALL_DIR | e.g., C:/Program Files/IDA Professional 9.2/ | Override current IDA Pro installation directory               |
| HCLI_CURRENT_IDA_PLATFORM    | auto-detected                                | Override IDA platform (e.g., "macos-aarch64", "linux-x86_64") |
| HCLI_CURRENT_IDA_VERSION     | auto-detected                                | Override IDA version (e.g., "9.1", "9.2")                     |
| HCLI_CURRENT_IDA_PYTHON_EXE  | auto-detected                                | Override Python executable path for IDA Pro                   |

In particular, if you haven't registered a default IDA installation, such as with:
- `hcli ida install ... --set-default`, or
- `hcli ida set-default /path/to/ida`

then you may need to set `HCLI_CURRENT_IDA_INSTALL_DIR` when using the plugin manager, so that HCLI can find IDA and its resources.

HCLI reads these variables, and `IDAUSR`, `IDADIR`, and `IDAPYTHON_VENV_EXECUTABLE`, each time it needs them. When HCLI runs inside IDA and no override is set, HCLI uses the values of the running IDA process.

See [How HCLI Finds IDA](ida-detection.md) for the full resolution order these overrides participate in.


## Cache & Storage

| Variable       | Default Value      | Purpose                                                      |
|----------------|--------------------|--------------------------------------------------------------|
| HCLI_CACHE_DIR | Platform-specific* | Override default cache directory for plugins and cached data |

*Platform-specific defaults:
- Linux: $XDG_CACHE_HOME/hex-rays/hcli/ or ~/.cache/hex-rays/hcli/
- Windows: %LOCALAPPDATA%\hex-rays\hcli\cache
- macOS: ~/Library/Caches/hex-rays/hcli/

## IDA Links

These variables control how `hcli ida open` handles an `ida://ke/...` link that contains a `url=` parameter. HCLI downloads the database from that URL, checks it against the SHA-256 in the URL, and opens it in IDA.
A web page can start this process, so the defaults ask for confirmation and block downloads from internal hosts.

| Variable                         | Default Value    | Purpose                                                                 |
|----------------------------------|------------------|-------------------------------------------------------------------------|
| HCLI_KE_DOWNLOADS_DIR            | ~/.ke/downloads  | Directory for downloaded databases                                      |
| HCLI_KE_DOWNLOADS_RETENTION_DAYS | 3                | HCLI deletes files in the downloads directory that are older than this number of days |
| HCLI_KE_ALLOW_PRIVATE_HOSTS      | false            | Allow downloads from hosts that do not have a public IP address. Set it for a server on your internal network |
| HCLI_KE_SKIP_CONFIRM             | false            | Open the downloaded database without the confirmation dialog            |
| HCLI_KE_MAX_DOWNLOAD_MB          | 0                | Maximum size of one download in MB. 0 sets no limit                     |
