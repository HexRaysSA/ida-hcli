# Porting your existing IDA Pro plugin

HCLI helps users discover, install, and manage IDA Pro plugins distributed via a central index.
As the author of an existing plugin, you can make the following updates so that your project is compatible with the new ecosystem.
If you have any questions, open an issue on this repo or email support@hex-rays.com.

We think that the HCLI plugin manager brings some nice benefits to plugin authors, and that its worthwhile to update your code for:

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
  - `.plugin.authors` and/or `.plugin.maintainers` must be provided, `.email` is required

and new optional fields:

  - `.plugin.pythonDependencies` is a list of packages on PyPI that will be installed
  - `.plugin.keywords` is a list of terms to help users searching for plugins
  - `.plugin.platforms` is recommended, defaults to all platforms. The possible values are: `windows-x86_64`, `linux-x86_64`, `macos-x86_64`, and `macos-aarch64`.
  - `.plugin.license` for the code license of your project
  - `.plugin.settings` is a list of descriptors of settings

If there's a problem with the `ida-plugin.json` file, then it will not be indexed by the plugin repository.
Unfortunately even things like trailing commas will break strict JSON parsers like the one used by hcli.
So, you can use `hcli plugin lint /path/to/plugin[.zip]` to check for problems and suggestions.


## 2. Package your plugin

You must distribute plugins via ZIP archives.
See [Plugin packaging and format](./plugin-packaging-and-format.md) for more details on the contents of the ZIP archives.
But essentially, collect the plugin files (Python source or `.dll`/`.so`/`.dylib` shared objects) and the `ida-plugin.json` and any other supporting resources into a single file.
a. For pure-Python plugins, you can probably rely on the source archive automatically attached to GitHub Releases and Tags.
b. For native plugins, you should consider using GitHub Actions to build the artifacts - and Hex-Rays is happy to provide templates and/or propose an initial workflow. You can see an example here: [milankovo/zydisinfo .../build.yml](https://github.com/milankovo/zydisinfo/blob/main/.github/workflows/build.yml)

## 3. Publish releases on GitHub

In the near term, you must use GitHub Releases to tag releases, because the plugin repository backend (initially) uses GitHub to discover and index available plugins.
You'll need to upload the plugin archives as attachments to the release (unless the default source archive is sufficient, which is usually true for pure-Python plugins).

Each day, an indexer looks for GitHub repositories that contain `ida-plugin.json` and inspects the releases for candidate plugins.
So once you've updated the metadata file and made a new release in GitHub, your plugin will soon show up automatically!

In other words, inclusion into the plugin repository is completely self-service - its just a matter of putting a well-formed plugin in a place we can find it.


## Example: migrating eset/DelphiHelper

Because this is a pure-Python plugin, the migration was very easy! Just add the metadata file and do releases on GitHub:

1. added `ida-plugin.json`: https://github.com/eset/DelphiHelper/pull/5/files
2. asked to start using GitHub Releases via the web interface
3. ...done


## Example: migrating milankovo/zydishelper

Because this is a plugin written in C++ and compiled to a native shared object, the migration took a little more work:

1. added `ida-plugin.json`: [PR#4](https://github.com/milankovo/zydisinfo/pull/4/files#diff-601834cd7516c6a40f96dda33295f21abd1f8e96f85095ab0823375f6479da3f)
2. added a [workflow for GitHub Actions](https://github.com/milankovo/zydisinfo/pull/4/files#diff-5c3fa597431eda03ac3339ae6bf7f05e1a50d6fc7333679ec38e21b337cb6721) to build the plugin
  a. use [HCLI](https://hcli.docs.hex-rays.com/) to fetch IDA Pro SDKs for 9.0, 9.1, and 9.2
  b. use [ida-cmake](https://github.com/allthingsida/ida-cmake) for configuration
  c. matrixed across Windows/Linux/macOS runners
  d. build the plugin
  e. upload to the GitHub Releases page
3. asked to start using [GitHub Releases](https://github.com/milankovo/zydisinfo/releases) via the web interface

While it took a little while to get working, this workflow should serve as a solid template for many plugins written in C++.
For example, I used it to kickstart the [build of the BinDiff plugin](https://github.com/HexRays-plugin-contributions/bindiff/blob/ci-gha/.github/workflows/build.yml).


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
  e. if its pure Python, then the default release source archive is likely sufficient
  f. if its a native plugin, you'll need to figure out how to build on GitHub Actions. this might take some time
  g. pay special attention to the two areas of complexity:
    i. Python (or other) dependencies
    ii. configuration/settings
7. `git checkout -b hr-test-release`
8. update `ida-plugin.json` so that this fork can temporarily be used by the plugin repository. commit and push them.
  a. set `.plugin.urls.repo` to the fork, like `https://github.com/HexRays-plugin-contributions/foo`
  b. add `.plugin.maintainers` entry for yourself
9. create a release using the `hr-test-release` branch
10. ensure the plugin can be installed: `hcli plugin install https://.../url/to/release/zip`


#### Migrating pure Python Plugins

For simple single-file plugins, all you need to do is add an `ida-plugin.json` file.
Then do releases via GitHub Actions and the automatically attached source archive will be the plugin archive.

In fact, most pure Python plugins can get away with no build step, and just tagging releases via GitHub Releases.

If there are multiple plugins in the same repo, this is ok, as long as they're in separate directories.
See "Multi-Plugin Archives" in [Plugin packaging and format](./plugin-packaging-and-format.md).

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
This is fine. IPyIDA is an example.
They can keep doing this, updating `pythonDependencies` to reference that package; or they can migrate to keeping the Python package as a relative import.


#### Migrating a Native Plugin

If its a native plugin, migrate the build configuration (if it exists) to GitHub Actions.
To acquire the SDK, either use a Git submodule or use HCLI to fetch it.
The latter is probably a better solution but requires an active IDA Pro license (but you can get one through the Plugin Contributor Program),
and enables you to build against 8.4, 9.0, 9.1, as well as 9.2+.
The open source SDK on GitHub only has tags for 9.2+.

Here's an example workflow:
https://github.com/milankovo/zydisinfo/pull/4

Once you have built the shared object files, package them up along with the `ida-plugin.json` file.
You can create one artifact per platform, or do a "fat" binary archive (see above).
Separate files might be easier; just make sure you set `idaPlatforms` in `ida-plugin.json` to reflect the contents, so we don't try to install .dll files on macOS.


## FAQ


#### Do I need to submit my plugin anywhere?

No, if you're using GitHub Releases to share plugin ZIP archives with valid `ida-plugin.json` files, then your plugins should be auto-included into the Hex-Rays plugin repository!
While there used to be an explicit submission process and web form, we've migrated to a self-service flow that's (hopefully) open and transparent.

In the future, we may re-open a web form or accept PRs to add plugins not hosted on GitHub, but this isn't supported quite yet.

#### What do I do if my plugin doesn't show up in the repo?

1. Ensure the plugin file (the ZIP archive) is well formed: `hcli plugin lint /path/to/plugin.zip`
2. Check that plugin is recognized by the indexer by inspecting [plugin-repository.json](https://github.com/HexRaysSA/plugin-repository/blob/v1/plugin-repository.json).
  a. ensure you can find your plugin in the search results: https://github.com/search?q=path%3A**%2Fida-plugin.json&type=code
  b. otherewise, you can propose to add your plugin to the explicit list here: [known-repositories.json](https://github.com/HexRaysSA/plugin-repository/blob/v1/known-repositories.txt)
3. Open an issue here and Hex-Rays will be happy to help debug: https://github.com/HexRaysSA/plugin-repository/issues


The indexer runs daily, so it might take a little time for your plugin to show up.
Feel free to ping us and we can manually trigger a rebuild to check for your new plugin.


#### How can I report a suspicious/malicious plugin?

Open an issue on the plugin repository and the Hex-Rays team will investigate and potentially update the index.


#### How does moderation work?

Plugins can be removed from the plugin repository so that HCLI does not serve them to users.

At a technical level, the plugin repository *is* the JSON file available [here](https://github.com/HexRaysSA/plugin-repository).
Its produced by an indexer that runs periodically, and uses a [denylist](https://github.com/HexRaysSA/plugin-repository/blob/v1/ignored-repositories.txt) to filter out repositories that Hex-Rays deems inappriopriate (malware, illegal, abusive, etc.).
Because the entire repository is within a git repository on GitHub, anyone can review the enforcement actions (such as adding to the denylist) and see what has happened.
We designed transparency into the system from the start, to help everyone understand and trust the infrastructure.





