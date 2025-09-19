# IDA Pro Plugin Manager

hcli can manage plugins for IDA Pro, letting users search, install, upgrade, and configure third-party plugins.
The underlying index of plugins is published at https://github.com/HexRaysSA/plugin-repository, and
 Hex-Rays maintains https://plugins.hex-rays.com as a website showing the available plugins.

Status: the plugin manager is roughly done, and we're now migrating plugins and documentation.


## IDA Pro Plugin Repository

plugins.hex-rays.com is a web interface to the collection of available IDA Pro plugins (the "plugin repository").
The plugin repository publishes a JSON document within https://github.com/HexRaysSA/plugin-repository.
This file is the index of all available plugins, their versions and metadata, and download URLs.
GitHub Actions runs regularly to upate the JSON document with plugins discovered across GitHub.
The raw index data is available here: https://raw.githubusercontent.com/HexRaysSA/plugin-repository/refs/heads/v1/plugin-repository.json

The plugin repository's GitHub Action watches for GitHub repositories that contain an `ida-plugin.json` file.
For each repo, it will watch for releases. When it sees a release, it will inspect the release archives for either
 source archives (pure-Python) or binary archives (containing .so/.dll/.dylib plugins).
In either case, it'll expect to find an `ida-plugin.json` file in the archive describing the plugin.
The service will index all the found archives and their metadata, and expose this to hcli
 (and/or other plugin managers, like a GUI version within IDA).

Note that the plugin repository requires more metadata than originally documented on the Hex-Rays website.
Keep reading to see what's required and how to migrate your plugin.


## hcli IDA Pro plugin manager

hcli uses the plugin repository JSON file to list/search for plugins and retrieve the download URL.
After various validation steps, hcli then extracts the archive subdirectory containing
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


## Plugin Archive Format

A Plugin Archive is a ZIP archive that contains an IDA plugin and its associated `ida-plugin.json` metadata file.
The metadata file should be found in the root directory of the plugin within the archive.

For example:

```
plugin.zip
├── ida-plugin.json
└── plugin.py
```

Or for a native plugin:

```
plugin.zip
├── ida-plugin.json
├── plugin.so
├── plugin.dylib
└── plugin.dll
```

### ida-plugin.json

The `ida-plugin.json` file is the marker for an IDA Pro plugin.
https://docs.hex-rays.com/user-guide/plugins/plugin-submission-guide#define-plugin-metadata-with-ida-plugin.json

A typical `ida-plugin.json` file might look like this:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "oplog",
    "entryPoint": "oplog_entry.py",
    "version": "0.1.2",
    "idaVersions": ["9.1", "9.2"],
    "description": "oplog is an IDA Pro plugin that records operations during analysis.",
    "license": "Apache 2.0",
    "categories": [
      "ui-ux-and-visualization"
    ],
    "pythonDependencies": ["pydantic>=2"],
    "urls": {
      "repository": "https://github.com/williballenthin/idawilli"
    },
    "authors": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }],
    "keywords": [
      "activity-tracking",
      "workflow-analysis",
      "reverse-engineering-methodology",
      "ai-training-data",
      "analysis-visualization"
    ]
  }
}
```

And a minimal `ida-plugin.json` could look like this:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "oplog",
    "entryPoint": "oplog_entry.py",
    "version": "0.1.2",
    "urls": {
      "repository": "https://github.com/williballenthin/idawilli"
    },
    "authors": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }]
  }
}
```

In addition to the fields described on the Hex-Rays website,
hcli requires the following fields in `ida-plugin.json`:

  - `version`: the version of the plugin archive
  - `urls.repository`: the repository that publishes the plugin
  - `authors` (or `maintainers`): name and/or email. Social media handles are ok!


### Source Archives and Binary Archives

For many pure-Python IDA plugins, source archives are often sufficient.
This means you don't have to create any GitHub Actions workflows; you just have to tag your releases in GitHub.

If you distribute your pure-Python IDA plugin via PyPI, [like IPyIDA does](https://github.com/eset/ipyida),
then the plugin directory in your source archive becomes very simple.
You can put the following content in a subdirectory of the project (preferred), or publish a separate repository for the metadata:


```
.
├── ida-plugin.json
└── entry_stub.py
```

With `ida-plugin.json`:

```json
{
  "IDAMetadataDescriptorVersion": 2,
  "plugin": {
    "name": "plugin1",
    "entryPoint": "entry_stub.py",
    "version": "1.0",
    "idaVersions": ["9.0", "9.1", "9.2"],
    "pythonDependencies": ["ida-plugin1==1.0"],
    "urls": {
      "repository": "https://github.com/foo/bar"
    },
    "authors": [{
      "email": "user@example.com"
    }]
  }
}
```

And the entry stub:


```py
from ida_plugin1.ida_plugin import PLUGIN_ENTRY
```

### "Fat" Binary Plugin Archives

Plugin archives can contain multiple compiled versions of a plugin, e.g., `foo-plugin.so` and `foo-plugin.dylib`.
In this case, the entry point must specify the bare path to the plugin, e.g., `foo-plugin`, and IDA will append the appropriate extension based on the platform.
You don't have to support all platforms. It's also ok to publish multiple "thin" archives, one for each platform.
But "fat" archives are convenient.

The file extensions must be exactly:
  - `.dll` - Windows x86-64
  - `.so` - Linux 86-64
  - `_x86_64.dylib` - macOS x86_64 (TODO: not yet supported by IDA)
  - `_aarch64.dylib` - macOS aarch64 (TODO: not yet supported by IDA)
  - `.dylib` - macOS Universal Binary, only if Intel/ARM dylibs aren't present. (note: you must use this for macOS today)

TODO: we need changes in IDA to support the non-Universal Binary paths. It only constructs paths like `.dylib` today.
TODO: we could let entry point also be a dict, mapping from platform to path. This also require changes to IDA.
However, Apple will soon drop support for Intel macs, so maybe we won't need x86_64 support for much longer.

Remember, plugin names must be unique within a plugin archive, which means:

Allowed:
```
root/
  plugin/
    ida-plugin.json (name: foo-plugin)
    foo-plugin.so
    foo-plugin.dylib
    foo-plugin.dll
```

Disallowed:
```
root/
  plugin-linux/
    ida-plugin.json (name: foo-plugin)
    foo-plugin.so
  plugin-windows/
    ida-plugin.json (name: foo-plugin)  <-- name collides!
    foo-plugin.dll
```

### (uncommon) Multi-Plugin Archives

Because the `ida-plugin.json` file marks the root of a plugin within the archive, an archive can contain multiple plugins:

```
plugins.zip
├── plugin1
│   ├── plugin1.py
│   └── ida-plugin.json
└── plugin2
    ├── plugin2.py
    └── ida-plugin.json
```

## Migrating Plugins to the Plugin Repository

(Rough notes:)

Plugins should try to use GitHub Actions for builds and GitHub Releases for tagging versions.

The plugin repository's discovery script uses GitHub Releases to identify new candidate versions. 
GH Releases is also a reasonable experience for users, due to the stable links, changelogs, and attached artifacts.
While we may add support for other hosting sites, GitHub is the only platform available today.

Pure Python plugins won't need a build step and can rely on source archives automatically attached to GitHub Release pages.
Other plugins can use any CI system they want (including manual builds, if they insist),
 but Hex-Rays provides examples and support for GitHub Actions.
Don't hesitate to reach out for help!

As you modify `ida-plugin.json`, use `hcli plugin lint /path/to/plugin/directory` to validate the contents and highlight issues.

Anyways, determine if the plugin is pure Python or a native plugin. More detailed notes follow.

Finally, remember to update the readme to explain that users should now use hcli instead of manual installing the plugin.


### Migrating pure Python Plugins

For simple single-file plugins, all you need to do is add an `ida-plugin.json` file.
Then do releases via GitHub Actions and the automatically attached source archive will be the plugin archive.

In fact, most pure Python plugins can get away with no build step, and just tagging releases via GitHub Releases.

If there are multiple plugins in the same repo, this is ok, as long as they're in separate directories.
See "Multi-Plugin Archives" above.

If a Python plugin relies on a third-party dependency, declare this in the `pythonDependencies` array in `ida-plugin.json`.

If there's many files related to the plugin, ensure they're all in the same directory (or nested subdirectory) as `ida-plugin.json`.
Python plugins can rely on imports relative to the entry point script, so the following are ok:

```
plugins.zip
└── plugin1
    ├── ida-plugin.json
    ├── plugin_entry.py
    └── myutils.py

# import myutils
```

or

```
plugins.zip
└── plugin1
    ├── ida-plugin.json
    ├── plugin_entry.py
    └── mylib
        ├── __init__.py
        └── foo

# import mylib.foo
```

Some plugins have published most of their code to PyPI via a tradional Python package, and then refer to this in a trivial entrypoint stub.
This is fine. They can keep doing this, updating `pythonDependencies` to reference that package;
or they can migrate to keeping the Python package as a relative import.


### Migrating a Native Plugin

If its a native plugin, migrate the build configuration (if it exists) to GitHub Actions.
To acquire the SDK, either use a Git submodule or use hcli to fetch it.
The latter is probably a better solution but requires an active IDA Pro license (but you can get one through the Plugin Contributor Program),
and enables you to build against 8.4, 9.0, 9.1, as well as 9.2+.
The open source SDK on GitHub only has tags for 9.2+.

Here's an example workflow:
https://github.com/williballenthin/zydisinfo/blob/gha-hcli/.github/workflows/build.yml

Once you have built the shared object files, package them up along with the `ida-plugin.json` file.
You can create one artifact per platform, or do a "fat" binary archive (see above).
Separate files might be easier; just make sure you set `idaPlatforms` in `ida-plugin.json` to reflect the contents, so we don't try to install .dll files on macOS.


### Experience Reports


#### idawilli plugins

Needed to add `ida-plugin.json files`. Used a template/copied from past projects. Took 5 minutes each.
Then did a GitHub release for the multiple IDA Python plugins.


#### IDA Terminal Plugin

Just needed to do a release in GitHub.

Took 15 minutes.


#### ipyida

Python source plugin with Python dependencies.
Notably it supports IDA 6.6+ - that will be hard to reproduce with hcli.

These are actually moderately complex:

```py
      install_requires=[
          'ipykernel>=4.6',
          'ipykernel>=5.1.4; python_version >= "3.8" and platform_system=="Windows"',
          'qtconsole>=4.3',
          'qasync; python_version >= "3"',
          'jupyter-client<6.1.13',
          'nbformat',
      ],
      extras_require={
          "notebook": [
              "notebook<7",
              "jupyter-kernel-proxy",
          ]
      },
```

However, all these dependencies are packaged into the `ipyida` Python package from PyPI.


```
.
├── install_from_ida.py
├── ipyida
│   ├── __init__.py
│   ├── ida_plugin.py
│   ├── ida_qtconsole.py
│   ├── ipyida_plugin_stub.py
│   ├── kernel.py
│   └── notebook.py
├── ipyida-screenshot.png
├── LICENSE
├── pycharm-screenshot.png
├── README.adoc
├── README.virtualenv.adoc
└── setup.py
```

I think we can get away with:

- remove `install_from_ida.py`
- move `ipyida/ipyida_plugin_stub.py` -> `./ipyida_plugin_stub.py`
- add `ida-plugin.json`
  - entrypoint: `ipyida_plugin_stub.py`
  - pythonDependencies: `ipyida`

In fact, I think we can do this in a new repository and reference the `ipyida` PyPI package:
https://github.com/ida-community-plugins/hcli-ipyida

Took 45 minutes, including updating docs/references with lessons learned.


#### milankovo/ida_export_scripts

https://github.com/milankovo/ida_export_scripts

Pure Python script with existing `ida-plugin.json`. Just needs a GitHub release.


#### mahmoudimus/ida-sigmaker

https://github.com/mahmoudimus/ida-sigmaker

ida-plugin.json had two issues:
- incorrect path for entrypoint
- path to a missing logo file

Both fixed in https://github.com/mahmoudimus/ida-sigmaker/pull/12

This demonstrates `ida-plugin.json` file doesn't receive much love today, and its easily out of date.

The author does releases, but only via tags.
So, need to add support for Python source plugins versioned with bare tags, rather than GitHub releases.


#### VirusTotal/vt-ida-plugin

https://github.com/VirusTotal/vt-ida-plugin

Doesn't have an `ida-plugin.json`. Python dependencies: `requests`.
Trivial metadata file, located in subdirectory `./plugin/`.

Still need to integrate the VT API key setting into the common framework.
Good test case for that design.

https://github.com/ida-community-plugins/vt-ida-plugin


#### mkYARA

https://github.com/fox-it/mkYARA

Needed to merge a bunch of pending PRs (the repo is not active): python3, IDA 9 support.
Needed to fix PyQt5 -> PySide6 stuff (making this IDA 9.2+ compatible).
Needed to move plugin to root so it could relative import the mkyara package;
 otherwise, would need to add pythonDependency on PyPI package that is out of date (no python3 support).

https://github.com/ida-community-plugins/mkYARA


#### yarka

https://github.com/AzzOnFire/yarka/pull/1

Very easy to port, but I had Claude do it, and it took a little bit to write ./plugins-AGENT.md.
Then I drafted the text for maintainers. This can also generally be reused.

There are some user preferences that the user can set by modifying the source code, like formatting of the yara rules.

#### Gepetto

https://github.com/JusticeRage/Gepetto/pull/105

Claude Code did it. Straightforward.

There's a config.ini file used to store settings.
