# Development Setup

## Prerequisites

- **Python 3.10+** - Required for development
- **uv** - Package manager (recommended)
- **Git** - Version control
- **IDA Pro** - For testing plugin functionality (optional)

## Getting Started

### Clone the Repository

```bash
git clone https://github.com/HexRaysSA/ida-hcli.git
cd ida-hcli
```

### Setup Development Environment

Using uv (recommended):
```bash
uv sync --extra app --extra dev
```

Using pip:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[app,dev]"
```

### Verify Installation

```bash
uv run hcli --version
```

### Testing

Run tests:
```bash
uv sync --extra app --extra test
uv run pytest
```

### Dependency Profiles

- `ida-hcli`: reusable library/core modules
- `ida-hcli[interactive]`: Click/Rich/Questionary CLI dependencies
- `ida-hcli[auth]`: Supabase authentication dependencies
- `ida-hcli[plugin]`: plugin-management dependencies such as `requests` and `pyyaml`
- `ida-hcli[app]`: full end-user CLI profile

## Project Structure

```
ida-hcli/
├── src/hcli/               # Main package
│   ├── commands/           # CLI command implementations
│   │   ├── auth/          # Authentication commands
│   │   ├── license/       # License management
│   │   └── share/         # File sharing
│   ├── lib/               # Core libraries
│   │   ├── api/           # API clients
│   │   ├── auth/          # Authentication logic
│   │   ├── config/        # Configuration management
│   │   └── util/          # Utilities
│   ├── env.py             # Environment configuration
│   └── main.py            # Entry point
├── tests/                 # Test suite
├── docs/                  # Documentation
├── pyproject.toml         # Project configuration
└── uv.lock               # Dependency lock file
```

## Architecture Overview

### Command Structure

Commands are organized hierarchically using Click:

```python
# src/hcli/commands/__init__.py
def register_commands(cli):
    cli.add_command(auth_group)
    cli.add_command(license_group)
    cli.add_command(share_group)
```

Each command group is implemented as a separate module with subcommands.

## Development Guidelines

### Adding New Commands

1. Create command module in appropriate group
2. Use `@async_command` decorator for async operations
3. Add `@require_auth` for commands that require authentication 
4. Follow existing patterns for error handling

Example:
```python
@async_command
@require_auth
async def my_command():
    """My new command."""
    # Implementation here
```

## Building and Packaging

### Build Package

```bash
uv build
```

### Install Local Build

```bash
pip install "dist/ida_hcli-*.whl[app]"
```

### Create Development Build

```bash
uv pip install -e ".[app]"
```
