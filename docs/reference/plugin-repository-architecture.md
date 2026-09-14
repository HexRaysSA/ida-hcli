# IDA Plugin Repository Architecture

## Core Components

The plugin system has several components:

[plugins.hex-rays.com](https://plugins.hex-rays.com/) is a web interface showing the available IDA plugins.

A **GitHub indexer** ([github.com/HexRaysSA/plugin-repository](https://github.com/HexRaysSA/plugin-repository)) runs regularly, discovers plugins across public GitHub repositories, and publishes a JSON index. The raw index data is available [on GitHub](https://raw.githubusercontent.com/HexRaysSA/plugin-repository/refs/heads/v1/plugin-repository.json).

The **community repository** at `community.plugins.hex-rays.com` mirrors this GitHub index. HCLI fetches from this URL by default. The **hexrays repository** at `hexrays.plugins.hex-rays.com` serves private plugins published by Hex-Rays, available to users with active IDA licenses. It requires authentication via `hcli login`.

HCLI reads its repositories from `.Settings.plugin-repositories` in `ida-config.json` and merges them client-side, so it always knows which repository served a plugin. Two are shipped and reserved: `community` (anonymous) and `hexrays` (authenticated). Additional repositories can be managed with `hcli plugin repo list | add | remove | set-default`. See [Plugin Manager](../user-guide/plugin-manager.md#plugin-repositories) for usage.

A reference may name a repository: `hcli plugin install hexrays/<name>`. Without a prefix, it resolves in the default repository (`community`). `hcli plugin search` spans every configured repository and reports any it could not reach.


## How It Works

The plugin repository's GitHub Action watches for GitHub repositories that contain an `ida-plugin.json` file.
For each repo, it will watch for releases. When it sees a release, it will inspect the release archives for either source archives (pure-Python) or binary archives (containing .so/.dll/.dylib plugins).
In either case, it'll expect to find an `ida-plugin.json` file in the archive describing the plugin.
The service will index all the found archives and their metadata, and expose this to HCLI
(and/or other plugin managers, like a planned GUI version within IDA).

!!! note

    The plugin repository requires additional metadata compared to the initial version documented on the Hex-Rays docs website.


## IDA Plugin Manager

HCLI fetches the plugin repository JSON from each configured repository to list/search for plugins and retrieve download URLs.
After various validation steps, HCLI then extracts the archive subdirectory containing
 `ida-plugin.json` into `$IDAUSR/plugins/`, and the plugin is installed.
If there are Python dependencies declared within the metadata file, then these are installed via pip first.
There are obvious upgrade and uninstallation routines, too.

### Plugin Installation Location

Plugins are installed to `$IDAUSR/plugins/`, where `$IDAUSR` is the IDA user directory: 

- **Windows**: `%APPDATA%\Hex-Rays\IDA Pro\`
- **macOS**: `~/Library/Application Support/IDA Pro/`
- **Linux**: `~/.idapro/`

It's possible to override `$IDAUSR` when running IDA, which can be helpful if you test across multiple versions:

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

During upgrades, the existing directory is replaced with the new version.
Uninstallation is as easy as deleting the directory.

### Plugin identity and name collisions

In the repository index, a plugin is identified by the pair `(name, repository URL)`. Two plugins with the same bare name from different repositories are distinct entries, and the repository URL is normalized (lowercased scheme/host/path, trailing slash stripped) before comparison so that cosmetic URL differences do not split a single plugin into two.

On disk, however, plugins still install as `$IDAUSR/plugins/<name>`. Name is therefore the installed-layout identity, and only one plugin with a given bare name can be installed at a time. When the repository contains multiple plugins with the same name, HCLI requires a qualified reference of the form `name@repository-url` (optionally with a version spec, e.g. `name==1.2.3@repository-url`). An installed plugin's local metadata records its source repository, so status and upgrade operations anchor their repository lookups on the installed plugin's host and stay consistent even when the bare name is ambiguous in the repository.
