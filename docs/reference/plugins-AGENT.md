Your goal is to package the given IDA Pro plugin git repository for the HCLI plugin infrastructure.
Read `../user-guide/plugin-manager.md` for context about how this works.

Here are your specific instructions. Ask for guidance and clarification along the way.

1. Read the project's readme and other documentation. Then read the main script and/or entrypoint.

2. Identify the following information and propose an `ida-plugin.json` file.
   Note the required wrapper: every field below lives under the `plugin` key,
   and `IDAMetadataDescriptorVersion` must be present and equal to `1`.
   HCLI rejects a manifest that puts these fields at the top level.

```
{
  "$schema": "https://hcli.docs.hex-rays.com/schemas/ida-plugin.json",
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": str,
    "entryPoint": path,
    "version": str, use existing version number, or if none, current date like `2025.9.24`
    "description": str, single concise sentence
    "license": str,
    "urls": {
      "repository": str
    },
    "authors": [{
      "name": str, handle or username is ok
      "email": str, optionl
    }],
    "idaVersions": [
      # the IDA versions the plugin supports, each one listed separately.
      # omitting this advertises every version, which is almost always wrong.
      # released values: "9.0", "9.0sp1", "9.1", "9.2", "9.3", "9.4"
      "9.2",
    ],
    "platforms": [
      # omit entirely for pure Python plugins: the default is all six platforms.
      # for native plugins, list exactly the platforms you ship a binary for.
      # values: "windows-x86_64", "windows-aarch64", "linux-x86_64",
      #         "linux-aarch64", "macos-x86_64", "macos-aarch64"
    ],
    "pythonDependencies": [
      # for pure Python plugins.
      # dependencies must be called out in the readme, not inferred from source.
      "packagename[>=version]",
    ],
    "dependencies": [
      # other plugins that should be installed alongside this one.
      # bare name, name==version, or name@host with optional version pin.
      # "dep-plugin-name",
      # "dep-plugin-name==1.0.0",
    ],
    "components": [
      # tightly-coupled sub-plugins bundled in the same archive.
      # each entry is a bare plugin name matching a subdirectory with its own ida-plugin.json.
      # no version pins or host qualifiers allowed.
      # "component-plugin-name",
    ],
    "settings": [
      # configuration values described in the readme or code
      # that would typically require manual source code editing or config file changes
      # but will be moved into the plugin system
      {
        "key": str, code identifier, like "api_key"
        "type": "string" or "boolean"
        "required": true or false
        "default": optional str or bool, default value
        "name": human readable name
        "documentation": optional human readable documentation, one line
        "validation_pattern": optional regex pattern, string settings only,
                              mutually exclusive with `choices`
        "choices": optional list of acceptable string values, string settings only,
                   mutually exclusive with `validation_pattern`
        "secret": optional bool, default false, string settings only.
                  set to true for API tokens and passwords: the input is masked
                  when prompting and the value is redacted in `config list` output
        "prompt": optional bool, default true, set to false to skip prompting during install.
                  requires `default` to be set
      }
    ],
    "categories": [
      # choose from the following values,
      # to help with discovery within the index and/or searching
      "disassembly-and-processor-modules"
      "file-parsers-and-loaders"
      "decompilation"
      "debugging-and-tracing"
      "deobfuscation"
      "collaboration-and-productivity"
      "integration-with-third-parties-interoperability"
      "api-scripting-and-automation"
      "ui-ux-and-visualization"
      "malware-analysis"
      "vulnerability-research-and-exploit-development"
      "other"
    ],
    "keywords": [
      # pick a few keywords that describe the plugin, its purpose, and related technologies,
      # to help with discovery within the index and/or searching
    ]
  }
}
```

3. Next develop a plan of action to package the plugin for the HCLI ecosystem. Ultrathink about this.
  a. Identify if this is a pure-Python plugin or a native plugin,
     and record the answer in `.plugin.platforms`:
     omit the field for a pure Python plugin, or list exactly the shipped platforms for a native one.
  b. Using the documentation, create a plan for any changes that need
     to be made to the repo and its code structure so that it can be packaged into a plugin archive.
    i.   move python dependencies into the `ida-plugin.json` file
    ii.  move settings into the `ida-plugin.json` file
    iii. build and package using GitHub Actions
  c. Try to minimize the number of changes you have to make.
  d. Show the plan and ask for confirmation.

4. If needed, propose a GitHub Actions workflow that will build the native plugin,
   using the following as an example:
   https://github.com/williballenthin/zydisinfo/blob/gha-hcli/.github/workflows/build.yml
