# Porting your existing IDA Pro plugin

hcli helps users discover, install, and manage IDA Pro plugins distributed via a central index.
As the author of an existing plugin, you can make the following updates so that your project is compatible with the new ecosystem.
If you have any questions, .

We think that the hcli plugin manager brings some nice benefits to plugin authors, and that its worthwhile to update your code for:

  - much easier plugin installation
  - better plugin discovery through the central index
  - easy Python dependency management

We'll also soon add plugin configuration management to handle things like user preferences, API key storage, etc.

If you're having trouble or don't have the bandwidth, please don't hesistate to reach out to us at Hex-Rays - we're here to support you.

Anyways, the key points are:

  1. update `ida-plugin.json`
  2. package your plugin into a ZIP archive
  3. publish releases on GitHub


For further details on how your plugin fits into the greater ecosystem, you can also review:

  - [Plugin packaging and format](./plugin-packaging-and-format.md)
  - [Plugin repository architecture](./plugin-repository-architecture.md)


## 1. Update `ida-plugin.json`

IDA 9.0 introduced `ida-plugin.json` as a way to declare metadata about plugins, such as name and entry point.
We're adding new required and optional fields to make this metadata more useful.

A minimal `ida-plugin.json` file now looks like:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "plugin1",
    "version": "1.0.0",
    "entryPoint": "plugin1.py",
    "urls": {
      "repository": "https://github.com/HexRaysSA/ida-hcli"
    },
    "authors": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }]
  }
}
```

and a more complete `ida-plugin.json` looks like:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "ida-terminal-plugin",
    "entryPoint": "index.py",
    "version": "1.0.0",
    "idaVersions": [
      "9.0",
      "9.1",
      "9.2"
    ],
    "platforms": [
      "windows-x86_64",
      "linux-x86_64",
      "macos-x86_64",
      "macos-aarch64",
    ],
    "description": "A lightweight terminal integration for IDA Pro that lets you open a fully functional terminal within the IDA GUI.\nQuickly access shell commands, scripts, or tooling without leaving your reversing environment.",
    "license": "MIT",
    "logoPath": "ida-plugin.png",
    "categories": [
      "ui-ux-and-visualization"
    ],
    "keywords": [
      "terminal",
      "shell",
      "cli",
    ],
    "pythonDependencies": [
      "pydantic>=2.12"
    ],
    "urls": {
      "repository": "https://github.com/williballenthin/idawilli"
    },
    "authors": [{
      "name": "Pierre-Alexandre Losson",
      "email": "palosson@hex-rays.com"
    }],
    "maintainers": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }],
    "settings": [
      {
        "key": "theme",
        "type": "string",
        "required": true,
        "default": "darcula",
        "name": "color theme",
        "documentation": "the color theme name, picked from https://windowsterminalthemes.dev/",
      }
    ]
  }
}
```

Here are the new required fields:

  - `.plugin.version` is now required
  - `.plugin.urls.repository` is required
  - `.plugin.authors` and/or `.plugin.maintainers` must be provided, `name` and/or `email` required

and the changed fields:

  - `.plugin.idaVersions` is recommended, and is now a list of strings, defaults to all versions

and new optional fields:

  - `.plugin.platforms` is recommended, defaults to all platforms. The possible values are: `windows-x86_64`, `linux-x86_64`, `macos-x86_64`, and `macos-aarch64`.
  - `.plugin.license`
  - `.plugin.pythonDependencies` is a list of packages on PyPI that will be installed
  - `.plugin.keywords` is a list of terms to help users searching for plugins
  - `.plugin.settings` is a list of descriptors of settings

If there's a problem with the `ida-plugin.json` file, then it will not be indexed by the plugin repository.
Unfortunately even things like trailing commas will break strict JSON parsers like the one used by hcli.
So, you can use `hcli plugin lint /path/to/plugin[.zip]` to check for problems and suggestions.


### Settings

hcli is aware of settings that plugins declare in `ida-plugin.json` and prompts users for their value
during installation. The settings are written into `ida-config.json` and can be queried at plugin runtime
using the [ida-settings](https://pypi.org/project/ida-settings/) (v3) Python package:

```py
import ida_settings

api_key = ida_settings.get_current_plugin_setting("openai_key")
```

Plugin authors should consider migrate to this configuration management system because there
 will be a single place to make edits (cli and gui), users don't have to manually edit source code/config files,
 and the data can be easily exported/imported.


## 2. Package your plugin


## 3. Publish releases on GitHub


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

### Hex-Rays guide for suggesting changes

1. navigate to the GitHub repo
2. fork to "HexRays-plugin-contributions" organization, using the defaults
3. check it out locally, like `git clone git@github.com:HexRays-plugin-contributions/foo.git`
4. `cd foo`
5. `git checkout -b ida-plugin-json`
6. add `ida-plugin.json` and any other migration changes. commit and push them. this will be submitted upstream as a PR.
  a. look at `plugins-AGENT.md` for some ideas
  b. `.plugin.urls.repo` should be the upstream repo
  c. `.plugin.authors` should be the original author
  d. `hcli plugin lint /path/to/plugin` can help identify issues
  e. if its pure Python, then the default release source archive is sufficient
  f. if its a native plugin, you'll need to figure out how to build on GitHub Actions. this might take some time
7. `git checkout -b hr-test-release`
8. update `ida-plugin.json` so that this fork can temporarily be used by the plugin repository. commit and push them.
  a. set `.plugin.urls.repo` to the fork, like `https://github.com/HexRays-plugin-contributions/foo`
  b. add `.plugin.maintainers` entry for yourself
9. create a release using the `hr-test-release` branch
10. ensure the plugin can be installed: `hcli plugin install https://.../url/to/release/zip`


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


