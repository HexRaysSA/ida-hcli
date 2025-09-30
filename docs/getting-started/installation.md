# Installation


This will automatically install the **hcli** standalone executable

=== "macOS and Linux"
    ```bash
    curl -LsSf https://hcli.docs.hex-rays.com/install | sh 
    ```
    Request a specific version by including it in the URL:
    ```bash
    curl -LsSf https://hcli.docs.hex-rays.com/install | sh -s -- --version 0.12.0
    ```

=== "Windows"
    ```cmd
    iwr -useb https://hcli.docs.hex-rays.com/install.ps1 | iex
    ```
    To request a specific version: 
    ```cmd
    iwr https://hcli.docs.hex-rays.com/install.ps1 -OutFile install.ps1
    ```
    Then run locally with the -Version argument 
    ```cmd
    .\install.ps1 -Version "0.12.0"
    ```

!!! tip

    The installation script may be inspected before use:

    === "macOS and Linux"

        ```console
        $ curl -LsSf https://hcli.docs.hex-rays.com/install | less
        ```

    === "Windows"

        ```pwsh-session
        PS> powershell -c "irm https://hcli.docs.hex-rays.com/install.ps1 | more"
        ```

    Alternatively, the binaries can be downloaded directly from [GitHub](#github-releases).

## Verify Installation

```bash
uv run hcli --version
```

## Next Steps

- [Authentication](authentication.md) - Set up your API credentials
- [Quick Start](quick-start.md) - Get started with basic commands
