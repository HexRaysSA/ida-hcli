# Installation

## Prerequisites

- Python 3.10 or higher
- IDA Pro (for plugin management features)

# Using pipx 

```bash
pipx install ida-hcli
hcli --help
```

# Using pip   

```bash
python3 -m venv ~/.venvs/ida-hcli
source ~/.venvs/ida-hcli/bin/activate
pip install ida-hcli
hcli --help
```

## Install from Source

```bash
git clone https://github.com/HexRaysSA/ida-hcli.git
cd ida-hcli
uv sync
uv run hcli --version
```

## Verify Installation

```bash
uv run hcli --version
```

## Next Steps

- [Authentication](authentication.md) - Set up your API credentials
- [Quick Start](quick-start.md) - Get started with basic commands