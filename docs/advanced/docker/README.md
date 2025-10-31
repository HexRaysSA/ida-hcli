# IDA Pro Docker Container

This Docker container provides a ready-to-use environment for analyzing binaries with IDA Pro using the idalib Python API.

## Prerequisites

- Docker or Podman installed
- Valid Hex-Rays credentials:
  - `HCLI_API_KEY`: Your Hex-Rays API key
  - `IDA_LICENSE_ID`: Your IDA Pro license ID

## Building the Image

Use `docker` or `podman`:

```bash
docker build \
  --platform=linux/amd64 \
  --build-arg HCLI_API_KEY="${HCLI_API_KEY}" \
  --build-arg IDA_LICENSE_ID="${IDA_LICENSE_ID}" \
  -t ida-pro:latest \
  .
```

**Note**: The IDA Pro EULA is automatically accepted during the installation process via hcli.

### Custom IDA Version or Product

You can customize the IDA version and product using additional build arguments:

```bash
podman build \
  --platform=linux/amd64 \
  --build-arg HCLI_API_KEY="${HCLI_API_KEY}" \
  --build-arg IDA_LICENSE_ID="${IDA_LICENSE_ID}" \
  --build-arg IDA_DOWNLOAD_ID="release/9.1/ida-pro/ida-pro_91_x64linux.run" \
  -t ida-pro:9.1 \
  .
```

Available build arguments:
- `HCLI_API_KEY` (required): Your Hex-Rays API key
- `IDA_LICENSE_ID` (required): Your IDA Pro license ID
- `IDA_DOWNLOAD_ID` (optional): Download ID for specific IDA version (default: `release/9.2/ida-pro/ida-pro_92_x64linux.run`)
  - Must be a Linux x64 installer (`.run` file)

## Running the Container

### Analyze a Binary with example script

```bash
docker run --rm \
  -v /path/to/your/binary:/analysis/binary \
  ida-pro:latest \
  -f /analysis/binary
```

**Note**: On SELinux systems (RHEL, Fedora, CentOS), use the `:Z` flag to relabel the volume for container access.

### Example Output

```
Analyzing binary: binary
Full path: /analysis/binary
--------------------------------------------------------------------------------

Successfully opened database for: binary
Total functions found: 14

sub_401000: 29 instructions
sub_401040: 20 instructions
sub_401070: 16 instructions
sub_4010A0: 115 instructions
sub_4011E0: 232 instructions
_main: 338 instructions
start: 77 instructions
_XcptFilter: 1 instructions
_initterm: 1 instructions
__setdefaultprecision: 6 instructions
UserMathErrorFunction: 2 instructions
nullsub_1: 1 instructions
_except_handler3: 1 instructions
_controlfp: 1 instructions
--------------------------------------------------------------------------------
Analysis complete!
```

## Container Details

### Base Image
- Python 3.13-slim (Debian Trixie)
- Platform: `linux/amd64` (x86_64)

### Installed Components
- IDA Pro 9.2 Professional (x64 Linux) - installed to `/opt/ida`
- IDA Pro license (`.hexlic` file) - copied to `/root/.idapro/`
- Python packages:
  - `idapro`: IDA Pro Python bindings
  - `ida-domain`: High-level IDA analysis API

### Environment Variables
- `IDADIR=/opt/ida`: IDA installation directory
- `PATH`: Includes `/opt/ida` for IDA binaries

### Build Process
The Dockerfile uses a multi-stage build:
1. **base**: Python 3.13-slim with curl and ca-certificates
2. **hcli-installer**: Downloads and installs hcli standalone binary
3. **ida-installer**: Uses hcli to download and install IDA Pro, configures license
4. **final**: Copies IDA installation and license, installs Python packages, adds entrypoint

## Security Considerations

**WARNING**: Build arguments containing secrets are stored in image layers and can be extracted!

- **Do NOT** push images with embedded credentials to public registries
- **Do NOT** share built images - others can extract your API keys and license IDs
- Build arguments (including `HCLI_API_KEY` and `IDA_LICENSE_ID`) are visible in:
  - Image history (`podman history ida-pro:latest`)
  - Image inspection (`podman inspect ida-pro:latest`)
  - Intermediate build layers

### Best Practices

1. **For Development**: Use environment variables (current approach)
   - Keep images local and private
   - Never push to registries

2. **For CI/CD**: Use ephemeral build secrets
   - GitHub Actions secrets
   - GitLab CI/CD variables
   - Jenkins credentials
