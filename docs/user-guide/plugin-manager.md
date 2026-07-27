# IDA Plugin Manager

The IDA Plugin Manager can help you discover, install, and manage IDA plugins distributed via a central index. It simplifies extending IDA capabilities, whether the plugins are written in IDAPython or compiled languages like C/C++.

The underlying index of plugins is published at [github.com/HexRaysSA/plugin-repository](https://github.com/HexRaysSA/plugin-repository),
 and Hex-Rays maintains [plugins.hex-rays.com](https://plugins.hex-rays.com) as a website showing the available plugins.

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



## As a user of IDA...

You'll want to know the HCLI commands:

```
❯ hcli plugin search 
❯ hcli plugin search [keyword or plugin-name]
❯ hcli plugin install <plugin-name>
❯ hcli plugin status
❯ hcli plugin upgrade <plugin-name>
❯ hcli plugin uninstall <plugin-name>
```

For offline or air-gapped environments, plugins can be installed from a plugin bundle archive. See [Plugin Bundles](../reference/plugin-bundle-spec.md) for details.

Plugins are written to `$IDAUSR/plugins`, which is typically `~/.idapro/plugins` on Unix-like systems, where IDA Pro will load them the next time the application is opened.

You can discover interesting plugins via:

  - `hcli plugin search` CLI program, or
  - [plugins.hex-rays.com](https://plugins.hex-rays.com) website, or
  - [github.com/HexRaysSA/plugin-repository](https://github.com/HexRaysSA/plugin-repository) raw index data.

HCLI supports installing plugins to be loaded by IDA 9.0 and newer.

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

### Installing a plugin that doesn't support your IDA

Every plugin declares the IDA versions and platforms it supports, and each archive in the repository is built for a specific combination of the two. `hcli plugin install` considers only the archives that match your environment, so a plugin with nothing built for your IDA is refused rather than installed in a state where it cannot load:

```console
❯ hcli plugin install oplog
Error: no version of 'oplog' supports IDA 9.2 on linux-x86_64
Use --allow-incompatible (-I) to install it anyway. The plugin may fail to load or crash IDA.
```

Pass `-I` (`--allow-incompatible`) to install it regardless. The same flag also covers plugins installed from a local directory, a `.zip`, or a URL, where there is no repository to filter but the plugin's own metadata still declares what it supports.

```console
❯ hcli plugin install -I oplog
warning: plugin does not support IDA version '9.2' (supported: 9.0, 9.1); installing anyway
Installed plugin: oplog==0.1.3
```

A compatible archive still wins whenever one exists. The filters are relaxed in stages, so the choice stays predictable: an archive built for your platform and your IDA version, then one built for your platform but a different IDA version, and only then anything at all. In practice you get the right binaries for your machine whenever the plugin ships them.

!!! warning "This is not a compatibility promise"

      Installing through the check means IDA loads a plugin its author never built for your setup. It may silently do nothing, fail to load, or crash IDA. Nothing is verified beyond the metadata that was just overridden, and a native plugin built for the wrong platform will not load at all.

The `(incompatible)` marker in `hcli plugin status` means something different: those are plugins already present in `$IDAUSR/plugins/` whose format HCLI does not understand. `--allow-incompatible` has no effect on them.

#### Moving an incompatible plugin to another IDA version

`hcli plugin upgrade` has no equivalent flag, on purpose: an upgrade should not quietly move you onto an archive that does not support your IDA. To change which archive is installed - after switching IDA versions, for instance - uninstall and install again:

```console
❯ hcli plugin uninstall oplog
❯ hcli plugin install -I oplog
```

This is also the way to pin a specific version, since the version spec is part of the install reference:

```console
❯ hcli plugin uninstall oplog
❯ hcli plugin install -I oplog==0.1.2
```


## As a plugin author...

Hex-Rays wants to help you package and distribute plugins for IDA!
Check out the following resources and don't hesitate to contact us for support:

  - [Plugin repository architecture](../reference/plugin-repository-architecture.md)
  - [Plugin packaging and format](../reference/plugin-packaging-and-format.md)
  - [Publishing your existing plugin](../reference/packaging-your-existing-plugin.md)
  - [Plugin bundles](../reference/plugin-bundle-spec.md) for offline distribution
