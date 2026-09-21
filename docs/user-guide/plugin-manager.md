# IDA Plugin Manager

The IDA Plugin Manager handles searching, installing, upgrading, and removing IDA plugins. It works with both IDAPython and native (C/C++) plugins.

Community plugins are indexed from public GitHub repositories and served through the Hex-Rays portal. Hex-Rays also publishes private plugins available to users with active IDA licenses. Browse available plugins at [plugins.hex-rays.com](https://plugins.hex-rays.com).

!!! note "Development status"

      The plugin manager is complete, and we’re now in the process of packaging plugins. Documentation updates are ongoing, and minor adjustments are expected.

## Quickstart

```console
❯ hcli plugin search
current platform: macos-aarch64
current version: 9.2

 bookmark-hints    0.1.3             https://github.com/williballenthin/idawilli
 colorize-calls    0.1.3             https://github.com/williballenthin/idawilli
 extensible-hints  0.1.3             https://github.com/williballenthin/idawilli
 hint-calls        0.1.3             https://github.com/williballenthin/idawilli
 oplog             0.1.3  installed  https://github.com/williballenthin/idawilli
 tag-func          0.1.3             https://github.com/williballenthin/idawilli

❯ hcli plugin install hint-calls
Installed plugin: hint-calls==0.1.3

❯ hcli plugin status
 oplog                               0.1.3
 hint-calls                          0.1.3
 (incompatible) yarka                0.7.0  found at: $IDAPLUGINS/yarka/
 (incompatible) IDA Terminal Plugin  0.0.3  found at: $IDAPLUGINS/IDA Terminal Plugin/
 (incompatible) DelphiHelper         1.21   found at: $IDAPLUGINS/DelphiHelper/
 (incompatible) IPyIDA               2.2    found at: $IDAPLUGINS/IPyIDA/
 (legacy) foo.py                            found at: $IDAPLUGINS/foo.py

Incompatible plugins don't work with this version of HCLI.
They might be broken or outdated. Try using `hcli plugin lint /path/to/plugin`.

Legacy plugins are old, single-file plugins.
They aren't managed by HCLI. Try finding an updated version in the plugin repository.
```

!!! note "Coming Soon"

      We plan to provide an IDA-native GUI for listing, installing, upgrading, and removing plugins in a future release.



## Python environment

Plugins that declare Python dependencies need a working Python environment. HCLI checks this before installing dependencies. If something is wrong, the install stops and points you to `hcli ida python doctor` for details. See [IDA's Python Environment](ida-python-environment.md) for setup.

If your Python setup works but does not match the recommended configuration, pass `--no-python-environment-check` to skip the check:

```console
❯ hcli plugin --no-python-environment-check install <name>
```

## Plugin repositories

HCLI fetches plugins from named repositories. Two are configured by default:

| Name | Description | Authentication |
| :--- | :---------- | :------------- |
| `community` | Community plugins indexed from public GitHub repositories | none (anonymous) |
| `hexrays` | Private plugins published by Hex-Rays | required (`hcli login`) |

The `community` repository is the default. A bare `hcli plugin install <name>` searches it. To install a private plugin from the `hexrays` repository, prefix the name:

```console
❯ hcli plugin install hexrays/some-private-plugin
```

If you're not logged in, the `hexrays` repository returns a 401 and HCLI tells you to authenticate. `hcli plugin search` spans all configured repositories and notes any it could not reach.

### Managing repositories

List, add, remove, or change the default repository:

```console
❯ hcli plugin repo list
community  https://community.plugins.hex-rays.com/plugin-repository.json  default reserved
hexrays    https://hexrays.plugins.hex-rays.com/plugin-repository.json    reserved

❯ hcli plugin repo add my-team https://plugins.example.com/repo.json
added plugin repository 'my-team' -> https://plugins.example.com/repo.json

❯ hcli plugin repo set-default my-team
default plugin repository is now 'my-team'

❯ hcli plugin repo remove my-team
removed plugin repository 'my-team'
```

The `community` and `hexrays` names are reserved and always point to their Hex-Rays URLs. Custom repositories can be added, renamed, or removed freely.

For offline or air-gapped environments like FLARE-VM, you can point HCLI at a local [plugin bundle](../reference/plugin-bundle-spec.md) instead of the online repositories. Pass `--repo` with a path to the bundle archive, and `search`, `install`, and `upgrade` all resolve from that local file without network access.

```console
❯ hcli plugin --repo ./malware-vm-tools.hcli-plugin-bundle.zip search
❯ hcli plugin --repo ./malware-vm-tools.hcli-plugin-bundle.zip install hint-calls
```

## As a user of IDA...

You'll want to know the HCLI commands:

```
❯ hcli plugin search 
❯ hcli plugin search [keyword or plugin-name]
❯ hcli plugin install <plugin-name>
❯ hcli plugin install <repo>/<plugin-name>
❯ hcli plugin status
❯ hcli plugin upgrade <plugin-name>
❯ hcli plugin uninstall <plugin-name>
❯ hcli plugin repo list
```

Plugins are written to `$IDAUSR/plugins`, which is typically `~/.idapro/plugins` on Unix-like systems, where IDA Pro will load them the next time the application is opened.

### Plugin dependencies

A plugin can declare other plugins as dependencies in its `ida-plugin.json`. When you install or upgrade such a plugin, HCLI fetches the declared dependencies from the same repository and installs them as independent top-level plugins. Dependencies that themselves declare further dependencies are resolved recursively, up to a depth of 10. If a dependency is already installed and satisfies the version requirement, it is skipped.

On upgrade, HCLI installs any newly-added dependencies and tells you about any that were removed from the manifest (they stay installed so you can remove them yourself if no longer needed).

On uninstall, HCLI lists installed dependencies and asks whether to remove them:

```console
❯ hcli plugin uninstall my-suite
Uninstalled plugin: my-suite
These plugins were listed as dependencies:
  dep-a    1.0.0
  dep-b    2.3.0
Remove them too? [y/N]
```

Pass `--yes` (`-y`) to confirm automatically in scripts. In non-interactive mode without `--yes`, dependencies are listed but not removed.

### Plugin suites

Some plugins are distributed as a suite: a root plugin that bundles tightly-coupled sub-plugins called components. Components share the suite's lifecycle. When you install or remove a suite, all its components go with it.

`hcli plugin status` shows a count of components next to each suite. Use `--show-components` to expand the listing:

```console
❯ hcli plugin status --show-components
 go-analysis-suite  2.0.0  (2 components)
   go-runtime-detector  1.0.0  (component)
   go-string-extractor  1.0.0  (component)
```

Components cannot be uninstalled individually. To remove a suite and all its components, uninstall the suite root:

```console
❯ hcli plugin uninstall go-analysis-suite
go-analysis-suite manages these plugins:
  go-runtime-detector                1.0.0
  go-string-extractor                1.0.0
Uninstall all? [Y/n]
```

You can discover interesting plugins via:

  - `hcli plugin search` CLI program, or
  - [plugins.hex-rays.com](https://plugins.hex-rays.com) website, or
  - [github.com/HexRaysSA/plugin-repository](https://github.com/HexRaysSA/plugin-repository) raw index data on GitHub.

HCLI supports installing plugins to be loaded by IDA 9.0 and newer.

### Plugin repositories

HCLI ships with two built-in repositories (`hexrays` and `community`) that provide the public plugin index. You can add your own named repositories that point to a JSON index file, a local directory of plugin archives, or a [plugin bundle](../reference/plugin-bundle-spec.md) zip.

```
❯ hcli plugin repo list
❯ hcli plugin repo add <name> <url>
❯ hcli plugin repo remove <name>
❯ hcli plugin repo set-default <name>
```

Repository URLs use the `https://` scheme for remote JSON indexes or `file://` for local paths. HCLI infers the repository type from what the path points to: a directory becomes a filesystem repository, a zip file containing `plugin-bundle.json` becomes a bundle repository, and anything else is treated as a JSON index.

You can scope plugin references to a specific repository with a `repo/` prefix (e.g., `hcli plugin install myrepo/plugin-name`). Unprefixed references resolve against the default repository.

### Offline and air-gapped environments

Environments without internet access (for example, a FLARE-VM instance with host-only networking) can use a plugin bundle as their sole repository. A plugin bundle is a self-contained zip archive with plugins and their Python dependencies for specific platforms. See [Plugin Bundles](../reference/plugin-bundle-spec.md) for the format.

To set this up, remove the default repositories (which require network access), add the bundle as a named repository, and set it as the default:

```console
❯ hcli plugin repo remove hexrays
removed plugin repository 'hexrays'

❯ hcli plugin repo remove community
removed plugin repository 'community'

❯ hcli plugin repo add offline file:///path/to/plugin-bundle.zip
added plugin repository 'offline' -> file:///path/to/plugin-bundle.zip

❯ hcli plugin repo set-default offline
default plugin repository is now 'offline'

❯ hcli plugin search
current platform: windows-x86_64
current version: 9.4

 plugin1  1.0.0  ...

❯ hcli plugin install plugin1
Installed plugin: plugin1==1.0.0
```

Dependencies are installed from the bundle's embedded wheelhouse, so pip does not need network access.

To restore the default configuration later, re-add the built-in repositories with their canonical URLs:

```console
❯ hcli plugin repo add hexrays https://hexrays.plugins.hex-rays.com/plugin-repository.json
❯ hcli plugin repo add community https://community.plugins.hex-rays.com/plugin-repository.json
```

### Disambiguating plugin names

When two plugins share the same bare name in the repository (for example, different forks of the same project), HCLI cannot tell which one you mean from the name alone. The `search`, `install`, and `upgrade` commands will print the ambiguous candidates and ask you to qualify the reference with the plugin's repository URL:

```console
❯ hcli plugin install ida-chat
Error: plugin name 'ida-chat' is ambiguous
Choose one of:
  ida-chat@https://github.com/HexRaysSA/ida-chat-plugin
  ida-chat@https://github.com/tanu360/ida-chat-plugin
```

You can pin the reference with `name@repository-url`, and optionally include a version spec:

```console
❯ hcli plugin install ida-chat@https://github.com/HexRaysSA/ida-chat-plugin
❯ hcli plugin install ida-chat==1.0.0@https://github.com/HexRaysSA/ida-chat-plugin
```

Because plugins install into `$IDAUSR/plugins/<name>`, only one plugin with a given bare name can be installed at a time. If you need to switch to a different same-named plugin from another repository, uninstall the current one first. Similarly, `upgrade` will not change the source repository: once a plugin is installed, its repository is recorded in the local metadata and upgrades are anchored to it.


## As a plugin author...

Hex-Rays wants to help you package and distribute plugins for IDA!
Check out the following resources and don't hesitate to contact us for support:

  - [Plugin repository architecture](../reference/plugin-repository-architecture.md)
  - [Plugin packaging and format](../reference/plugin-packaging-and-format.md)
  - [Publishing your existing plugin](../reference/packaging-your-existing-plugin.md)
  - [Plugin bundles](../reference/plugin-bundle-spec.md) for offline distribution

Plugin authors can declare loose dependencies on other plugins via `dependencies`, or bundle tightly-coupled sub-plugins via `components`, in `ida-plugin.json`. See [Plugin packaging and format](../reference/plugin-packaging-and-format.md) for details on both fields.
