# IDA HCLI

[![PyPI version](https://badge.fury.io/py/ida-hcli.svg)](https://badge.fury.io/py/ida-hcli)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)


![](docs/assets/screenshot.png)

A modern command-line interface for managing IDA Pro licenses, plugins, ...

# Documentation

See [https://hcli.docs.hex-rays.com/](hcli.docs.hex-rays.com/)

## Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details on how to:

- Report bugs and suggest features
- Submit pull requests with proper testing
- Set up your development environment with Hatch
- Generate and update documentation automatically

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Issues and Support

- **Bug Reports & Feature Requests**: [GitHub Issues](https://github.com/HexRaysSA/ida-hcli/issues)
- **Questions & Discussions**: [Discussions](https://community.hex-rays.com/)
- **Documentation**: Auto-generated from source code at build time
- **Commercial Support**: Contact support@hex-rays.com
- **Hex-Rays Website**: [hex-rays.com](https://hex-rays.com/)

## Development

### Prerequisites

- Python 3.10 or higher
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
# Clone the repository
git clone https://github.com/HexRaysSA/ida-hcli.git
cd ida-hcli

# Install dependencies
uv sync

# Run in development mode
uv run hcli --help
```

### Build System

```bash
# Install with development dependencies
uv sync --extra dev 

# Build package
uv build 

# Run development tools
uv run ruff format
uv run ruff check --fix
uv run ruff check --select I --fix
```

### Documentation

Documentation is **automatically generated** from source code:

```bash
# Build documentation
uv run mkdocs build

# Serve documentation locally
uv run mkdocs serve

# Documentation includes:
# - CLI commands (from Click help text)
# - API reference (from Python docstrings)
# - Usage examples (auto-generated)
```

### Testing

```bash
# Run tests
uv run pytest

# Test CLI commands
uv run hcli whoami
uv run hcli plugin list
```


See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed development guidelines.