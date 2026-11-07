# IDA Pro Docker Container

This Docker container provides a ready-to-use environment for analyzing binaries with IDA Pro using the idalib Python API.

## Prerequisites

- Docker with BuildKit (the default builder) or Podman installed
- Valid Hex-Rays credentials:
  - `HCLI_API_KEY`: Your Hex-Rays API key
  - `IDA_LICENSE_ID`: Your IDA Pro license ID

## Building the Image

Use `docker` or `podman`:

```bash
docker build \
  --secret id=hcli_api_key,env=HCLI_API_KEY \
  --build-arg IDA_LICENSE_ID="${IDA_LICENSE_ID}" \
  -t ida-pro:latest \
  .
```

The API key is passed as a build secret, which reads it from the `HCLI_API_KEY` environment variable.

The image is built for the architecture of the build host: `linux/amd64` on x86_64 hosts and `linux/arm64` on ARM64 hosts such as Apple Silicon Macs. To build for another architecture, add `--platform=linux/amd64` or `--platform=linux/arm64`; the build then runs under emulation.

**Note**: The IDA Pro EULA is automatically accepted during the installation process via hcli.

### Custom IDA Version or Product

You can customize the IDA version and product using additional build arguments:

```bash
podman build \
  --secret id=hcli_api_key,env=HCLI_API_KEY \
  --build-arg IDA_LICENSE_ID="${IDA_LICENSE_ID}" \
  --build-arg IDA_DOWNLOAD_ID="ida-pro:9.5" \
  -t ida-pro:9.5 \
  .
```

Build secret:
- `hcli_api_key` (required): Your Hex-Rays API key

Available build arguments:
- `IDA_LICENSE_ID` (required): Your IDA Pro license ID
- `IDA_DOWNLOAD_ID` (optional): Tag or download ID of the IDA installer (default: `ida-pro:latest`)
  - A tag such as `ida-pro:latest` or `ida-pro:9.5` selects the Linux installer for the image architecture
  - A full download ID must be a Linux installer (`.run` file) for the image architecture: `x64linux` for `linux/amd64`, `armlinux` for `linux/arm64`, for example `release/9.5/ida-pro/ida-pro_95_armlinux.run`
  - On `linux/arm64`, the IDA version must have a Linux ARM64 build

## Running the Container

### Analyze a Binary with example script

```bash
docker run --rm \
  -v /path/to/your/binary:/analysis/binary \
  ida-pro:latest \
  /analysis/binary
```

**Note**: On SELinux systems (RHEL, Fedora, CentOS), use the `:Z` flag to relabel the volume for container access.

### Example Output

The example script prints the address and name of each function:

```
0x401000: sub_401000
0x401040: sub_401040
0x401070: sub_401070
...
```

## Container Details

### Base Image
- Python 3.13-slim (Debian Trixie)
- Platform: `linux/amd64` (x86_64) or `linux/arm64` (ARM64), the build platform unless `--platform` is given

### Installed Components
- IDA Pro (latest release by default, Linux build for the image architecture) - installed to `/opt/ida`
- IDA Pro license (`.hexlic` file) - copied to `/root/.idapro/`
- Python virtualenv at `/root/.idapro/venv` with:
  - `idapro`: IDA Pro Python bindings
  - `ida-domain`: High-level IDA analysis API

### Environment Variables
- `IDADIR=/opt/ida`: IDA installation directory
- `IDAPYTHON_VENV_EXECUTABLE=/root/.idapro/venv/bin/python`: Python interpreter for IDA's virtualenv
- `PATH`: Includes `/opt/ida` for IDA binaries

### Build Process
The Dockerfile uses a multi-stage build:
1. **base**: Python 3.13-slim with curl and ca-certificates
2. **hcli-installer**: Downloads and installs hcli standalone binary
3. **ida-installer**: Uses hcli to download and install IDA Pro with `--create-python-environment`, configures license
4. **final**: Copies IDA installation with its license and venv, installs Python packages into the venv, adds entrypoint

## Security Considerations

The API key is passed as a build secret. It is mounted only while hcli installs IDA, and is not stored in the build history, the build cache, or the image layers. Do not pass it as a build argument instead: build arguments are stored in the build history and cache.

`IDA_LICENSE_ID` is a build argument, so it is stored in the build history and cache of the `ida-installer` stage.

**WARNING**: The final image contains your IDA license file (`.hexlic`), which others can extract.

- **Do NOT** push the image to public registries
- **Do NOT** share the built image

In CI/CD, pass the API key from the CI system's secret store, such as GitHub Actions secrets or GitLab CI/CD variables, to the build as the `hcli_api_key` secret.
