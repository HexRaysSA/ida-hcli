# CLI Commands Reference

HCLI provides a rich set of commands for managing IDA Pro installations, licenses, downloads, and more.

## Available Commands

To see all available commands with descriptions:

```bash
hcli commands
```

For help with any specific command or subcommand:

```bash
hcli --help                    # Main help
hcli auth --help               # Auth commands help  
hcli download --help           # Download command help
hcli license get --help        # Specific subcommand help
```

## Main Command Categories

- **Authentication**: `hcli auth`, `hcli login`, `hcli logout`, `hcli whoami`
- **Downloads**: `hcli download`
- **Licenses**: `hcli license`
- **Installations**: `hcli ida`
- **File Sharing**: `hcli share`
- **Extensions**: `hcli extension`
- **Utilities**: `hcli commands`, `hcli update`