# IDA Plugin Repository Architecture

## Core Components

1. **User-facing portal showcasing the plugins** - The [plugins.hex-rays.com](https://plugins.hex-rays.com/) is a web interface to the collection of available IDA Pro plugins.
2. **JSON Index** - The plugin repository publishes a JSON document within [github.com/HexRaysSA/plugin-repository](https://github.com/HexRaysSA/plugin-repository). This file is the index of all available plugins, their versions and metadata, and download URLs.
3. **GitHub Actions** - GitHub Actions runs regularly to upate the JSON document with plugins discovered across GitHub. The raw index data is available [here](https://raw.githubusercontent.com/HexRaysSA/plugin-repository/refs/heads/v1/plugin-repository.json).


## How It Works

The plugin repository's GitHub Action watches for GitHub repositories that contain an `ida-plugin.json` file.
For each repo, it will watch for releases. When it sees a release, it will inspect the release archives for either source archives (pure-Python) or binary archives (containing .so/.dll/.dylib plugins).
In either case, it'll expect to find an `ida-plugin.json` file in the archive describing the plugin.
The service will index all the found archives and their metadata, and expose this to HCLI
 (and/or other plugin managers, like a GUI version within IDA).

!!! note

    The plugin repository requires additional metadata compared to the initial version documented on the Hex-Rays docs website.


## HCLI IDA Plugin Manager

HCLI uses the plugin repository JSON file to list/search for plugins and retrieve the download URL.
After various validation steps, HCLI then extracts the archive subdirectory containing
 `ida-plugin.json` into `$IDAUSR/plugins/`, and the plugin is installed.
If there are Python dependencies declared within the metadata file, then these are installed via pip first.
There are obvious upgrade and uninstallation routines, too.

### Plugin Installation Location

Plugins are installed to `$IDAUSR/plugins/`, where `$IDAUSR` is the IDA user directory: 

- **Windows**: `%APPDATA%\Hex-Rays\IDA Pro\`
- **macOS**: `~/Library/Application Support/IDA Pro/`
- **Linux**: `~/.idapro/`

Its possible to override `$IDAUSR` when running IDA, which can be helpful if you test across multiple versions:

```
$ export IDAUSR=~/.idapro91/
$ hcli plugin install ipyida
$ ~/software/ida-9.1/ida
```

Each plugin is installed in its own subdirectory within `plugins/`. For example, installing the "oplog" plugin creates:
```
$IDAUSR/plugins/oplog/
├── ida-plugin.json
├── oplog_entry.py
└── (other plugin files)
```

The directory name matches the plugin name from `ida-plugin.json`.
This is why the contents of `name` are fairly restrictive. They should also be globally unique.
We'll address collisions in plugin names by taking into account the code repository, too.

During upgrades, the existing directory is replaced with the new version.
Uninstallation is as easy as deleting the directory.
