# IDA Pro Plugin Manager

hcli can help you discover, install, and manage IDA Pro plugins distributed via a central index.
It should be very easy for you extend the capabilities of IDA Pro with plugins,
 whether they are written in IDAPython or compiled languages like C/C++.

The underlying index of plugins is published at https://github.com/HexRaysSA/plugin-repository, and
 Hex-Rays maintains https://plugins.hex-rays.com as a website showing the available plugins.

Status: the plugin manager is roughly done, and we're now migrating plugins and documentation.

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

Incompatible plugins don't work with this version of hcli.
They might be broken or outdated. Try using `hcli plugin lint /path/to/plugin`.

Legacy plugins are old, single-file plugins.
They aren't managed by hcli. Try finding an updated version in the plugin repository.
```

We'll also work to provide an IDA-native GUI for list/install/upgrade/removing plugins in the future.


## As a user of IDA Pro...

you'll want to know the hcli commands:

```
❯ hcli plugin search 
❯ hcli plugin search [keyword or plugin name]
❯ hcli plugin install plugin-name
❯ hcli plugin status
❯ hcli plugin upgrade plugin-name
❯ hcli plugin uninstall plugin-name
```

Plugins are written to `$IDAUSR/plugins`, which is typically `~/idapro/plugins` on Unix-like systems,
where IDA Pro will load them the next time the application is opened.

You can discover interesting plugins via:

  - `hcli plugin search` CLI program, or
  - https://plugins.hex-rays.com website, or
  - https://github.com/HexRaysSA/plugin-repository raw index data.

hcli supports installing plugins to be loaded by IDA 9.0 and newer.


## As a plugin author...

Hex-Rays wants to help you package and distribute plugins for IDA Pro!
Check out the following resources and don't hesitate to contact us for support:

  - [Porting your existing plugin](./porting-your-existing-plugin.md)
  - [Plugin packaging and format](./plugin-packaging-and-format.md)
  - [Plugin repository architecture](./plugin-repository-architecture.md)
