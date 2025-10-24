You goal is to migrate the given IDA Pro plugin git repository to use the new HCLI plugin infrastructure.
Read `./plugin-manager.md` for context about how this works.

Here are your specific instructions. Ask for guidance and clarification along the way.

1. Read the project's readme and other documentation. Then read the main script and/or entrypoint.

2. Identify the following information and propose an `ida-plugin.json` file:

```
{
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
  "pythonDependencies": [
    # for pure Python plugins.
    # dependencies must be called out in the readme, not inferred from source.
    "packagename[>=version]",
  ],
  "settings": [
    # configuration values described in the readme or code
    # that would typically require manual source code editing or config file changes
    # but will be migrated into the plugin system
    {
      "key": str, code identifier, like "api_key"
      "type": "string"
      "required": true or false
      "default": optional str, default value
      "name": human readable name
      "documentation": optional human readable documentation, one line
      "validation_pattern": optional regex pattern
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
```

3. Next develop a plan of action to migrate the plugin into the new hcli ecosystem. Ultrathink about this.
  a. Identify if this is a pure-Python plugin or a native plugin.
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
