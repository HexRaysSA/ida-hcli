# HCLI Documentation

![](assets/screenshot.png)

**HCLI** is a modern command-line interface for managing IDA Pro licenses, installations, and plugins.
Its designed for both interactive use and automation workflows.

Head over to [Getting Started](getting-started/quick-start.md) to learn more.

## Installation (Quick Start)

This will automatically install the **HCLI** standalone executable

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

## Key Features

- **Install IDA** - Download and install IDA, interactively or headlessly
- **License Management** - Install and manage your IDA Pro licenses
- **Plugin Manager** - discover, install, and configure IDA Pro plugins.
- **File Sharing** - Securely share analysis files with Hex-Rays for support tickets

