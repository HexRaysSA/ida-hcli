# IDA Plugin Manager

Plugin Manager can help you discover, install, and manage IDA plugins distributed via a central index. It simplifies extending IDA capabilities, whether the plugins are written in IDAPython or compiled languages like C/C++.

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

Plugins are written to `$IDAUSR/plugins`, which is typically `~/.idapro/plugins` on Unix-like systems, where IDA Pro will load them the next time the application is opened.

You can discover interesting plugins via:

  - `hcli plugin search` CLI program, or
  - [plugins.hex-rays.com](https://plugins.hex-rays.com) website, or
  - [github.com/HexRaysSA/plugin-repository](https://github.com/HexRaysSA/plugin-repository) raw index data.

HCLI supports installing plugins to be loaded by IDA 9.0 and newer.


## As a plugin author...

Hex-Rays wants to help you package and distribute plugins for IDA!
Check out the following resources and don't hesitate to contact us for support:

  - [Plugin repository architecture](../reference/plugin-repository-architecture.md)
  - [Plugin packaging and format](../reference/plugin-packaging-and-format.md)
  - [Publishing your existing plugin](../reference/packaging-your-existing-plugin.md)
