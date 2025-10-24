# CI/CD Integration

## Overview

HCLI is designed to work in automated environments like CI/CD pipelines. This guide shows you how to:

- Authenticate HCLI in automated environments
- Install IDA Pro in CI/CD runners
- Download IDA SDKs for building plugins
- Test IDA Pro plugins automatically

Using HCLI in CI/CD enables you to:

- **Automate plugin builds** across multiple IDA versions
- **Test plugins** against different IDA releases
- **Build native plugins** using the IDA SDK
- **Verify plugin compatibility** before releasing
- **Deploy plugins** to the Hex-Rays plugin repository

## Authentication for CI/CD

### Using API Keys

API keys are the recommended authentication method for automated environments. Unlike OAuth, API keys don't require browser interaction and can be securely stored as CI/CD secrets.

### Creating an API Key

First, create an API key for your CI/CD system:

```bash
hcli auth key create --name "my-project-ci"
The key will be displayed only once, so make sure to save it in a secure place.
? Do you want to create a new API key my-project-ci? (Y/n) Yes
API key created: hrp-1-abc123def456...
```

**Important:** Save this key immediately - it won't be shown again!

### Setting the Environment Variable

HCLI automatically uses the `HCLI_API_KEY` environment variable for authentication:

```bash
export HCLI_API_KEY=hrp-1-abc123def456...
```

When this environment variable is set, HCLI will use it for all API requests without requiring interactive login.

Verify authentication:

```bash
hcli whoami
You are logged in as user@example.com using an API key from HCLI_API_KEY environment variable
```

### Security Best Practices

1. **Never commit API keys to version control** - Always use CI/CD secret management
2. **Use descriptive key names** - Include the project and purpose (e.g., "zydis-github-actions")
3. **Create separate keys per project** - This allows independent key rotation and revocation
4. **Rotate keys periodically** - Revoke old keys and create new ones regularly
5. **Monitor key usage** - Use `hcli auth key list` to check when keys were last used
6. **Revoke unused keys** - Clean up keys that are no longer needed

For more details, see [Authentication](../getting-started/authentication.md) and [Environment Variables](../reference/environment-variables.md).

## GitHub Actions

### Installing IDA Pro

This example shows how to install IDA Pro in a GitHub Actions workflow for developing plugins that require a full IDA installation:

```yaml
name: Test Plugin

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install HCLI
        run: |
          pip install ida-hcli

      - name: Authenticate HCLI
        env:
          HCLI_API_KEY: ${{ secrets.HCLI_API_KEY }}
        run: |
          hcli whoami

      - name: Install IDA Pro
        env:
          HCLI_API_KEY: ${{ secrets.HCLI_API_KEY }}
        run: |
          hcli ida install \
            --yes \
            --accept-eula \
            --set-default \
            --license-id ${{ secrets.IDA_LICENSE_ID }} \
            --download-id release/9.2/ida-pro/ida-pro_92_x64linux.run
```

#### GitHub Actions Secrets

Configure these secrets in your GitHub repository settings (Settings → Secrets and variables → Actions):

- `HCLI_API_KEY` - Your HCLI API key from `hcli auth key create`
- `IDA_LICENSE_ID` - Your IDA license ID (e.g., "96-0000-0000-01")

### Downloading IDA SDK

This example shows how to download the IDA SDK for building native plugins:

```yaml
name: Build Native Plugin

on:
  push:
    branches: [ main ]
  release:
    types: [ created ]

jobs:
  build:
    strategy:
      matrix:
        environment:
          #
          # 9.2
          #
          - os: ubuntu-latest
            os_name: "linux"
            ida_version: "9.2"
            sdk_slug: "release/9.2/sdk-and-utilities/idasdk92.zip"
            sdk_subdir: "src/"

          - os: windows-latest
            os_name: "windows"
            ida_version: "9.2"
            sdk_slug: "release/9.2/sdk-and-utilities/idasdk92.zip"
            sdk_subdir: "src/"

          - os: macos-latest
            os_name: "macos"
            ida_version: "9.2"
            sdk_slug: "release/9.2/sdk-and-utilities/idasdk92.zip"
            sdk_subdir: "src/"

            # 
            # 9.1: gooMBA main doesn't build for 9.1
            # 
            - os: ubuntu-latest
              os_name: "linux"
              ida_version: "9.1"
              sdk_slug: "release/9.1/sdk-and-utilities/idasdk91.zip"
              sdk_subdir: "idasdk91/"
          
            - os: windows-latest
              os_name: "windows"
              ida_version: "9.1"
              sdk_slug: "release/9.1/sdk-and-utilities/idasdk91.zip"
              sdk_subdir: "idasdk91/"
          
            - os: macos-latest
              os_name: "macos"
              ida_version: "9.1"
              sdk_slug: "release/9.1/sdk-and-utilities/idasdk91.zip"
              sdk_subdir: "idasdk91/"

            #
            #  9.0: gooMBA main doesn't build for 9.0
            # 
            - os: ubuntu-latest
              os_name: "linux"
              ida_version: "9.0"
              sdk_slug: "release/9.0/sdk-and-utilities/idasdk90.zip"
              sdk_subdir: "idasdk90/"
          
            - os: windows-latest
              os_name: "windows"
              ida_version: "9.0"
              sdk_slug: "release/9.0/sdk-and-utilities/idasdk90.zip"
              sdk_subdir: "idasdk90/"
          
            - os: macos-latest
              os_name: "macos"
              ida_version: "9.0"
              sdk_slug: "release/9.0/sdk-and-utilities/idasdk90.zip"
              sdk_subdir: "idasdk90/"

    steps:
      - name: Setup MSBuild
        if: matrix.environment.os_name == 'windows'
        uses: microsoft/setup-msbuild@v1.1
  
      - name: Setup Visual Studio 2022
        if: matrix.environment.os_name == 'windows'
        uses: ilammy/msvc-dev-cmd@v1
        with:
          vsversion: 2022

      - name: Setup uv
        uses: astral-sh/setup-uv@v5

      - name: Download IDA SDK ${{ matrix.environment.ida_version }}
        run: |
          uvx --from ida-hcli hcli --disable-updates download ${{ matrix.environment.sdk_slug }}
          unzip idasdk*.zip -d ./ida-temp/
          mv ./ida-temp/${{ matrix.environment.sdk_subdir }} ./ida-sdk
        env:
          HCLI_API_KEY: ${{ secrets.HCLI_API_KEY }}

      - name: Setup IDA SDK environment
        if: matrix.environment.os_name == 'windows'
        working-directory: ./ida-sdk/
        run: |
          # via: https://hex-rays.com/blog/building-ida-python-on-windows
          set __EA64__=1
          set NDEBUG=1
          make env

      # do build referring to the SDK directory,
      # such as with ida-cmake
      # see example workflows below
```

Examples:
  - [milankovo/zydisinfo](https://github.com/milankovo/zydisinfo/blob/1f01106/.github/workflows/build.yml)
  - [HexRaysSA/goomba](https://github.com/HexRaysSA/goomba/blob/7dc32ef/.github/workflows/build.yml)
  - [HexRays-plugin-contributions/bindiff](https://github.com/HexRays-plugin-contributions/bindiff/blob/e325b04/.github/workflows/build.yml)


## Common Patterns

### Matrix Builds Across IDA Versions

Test your plugin against multiple IDA versions:

```yaml
jobs:
  test:
    strategy:
      matrix:
        ida-version: ['9.0', '9.1', '9.2']
        python-version: ['3.11', '3.12']

    steps:
      - name: Install IDA ${{ matrix.ida-version }}
        env:
          HCLI_API_KEY: ${{ secrets.HCLI_API_KEY }}
        run: |
          # Determine the correct installer filename
          IDA_VERSION_NODOT=$(echo "${{ matrix.ida-version }}" | tr -d '.')
          INSTALLER="ida-pro_${IDA_VERSION_NODOT}_x64linux.run"

          hcli ida install \
            --yes \
            --accept-eula \
            --set-default \
            --license-id ${{ secrets.IDA_LICENSE_ID }} \
            --download-id release/${{ matrix.ida-version }}/ida-pro/$INSTALLER
```

### Enable Debug Logging

```yaml
- name: Debug HCLI
  env:
    HCLI_DEBUG: true
    HCLI_LOG_LEVEL: DEBUG
  run: |
    hcli whoami
    hcli ida list
```

### Dry Run Installations

```yaml
- name: Test installation (dry run)
  run: |
    hcli ida install \
      --dry-run \
      --yes \
      --accept-eula \
      --license-id ${{ secrets.IDA_LICENSE_ID }} \
      --download-id release/9.2/ida-pro/ida-pro_92_x64linux.run
```

## Next Steps

- Review [Environment Variables](../reference/environment-variables.md) for all configuration options
- See [Packaging Your Plugin](../reference/packaging-your-existing-plugin.md) for automated plugin builds
- Check [Authentication](../getting-started/authentication.md) for more on API key management
- Explore real-world examples in the [zydisinfo](https://github.com/milankovo/zydisinfo/blob/main/.github/workflows/build.yml) repository
