You goal is to migrate the given IDA Pro plugin git repository to use the new hcli plugin infrastructure.
Read `./plugins.md` for context about how this works.

Here are your specific instructions. Ask for guidance and clarification along the way.

0. Since your changes will go into a PR, use the `upstream` remote as the source of truth for the repo, not `origin`.

1. Identify the following information:

```
{
  "name": str,
  "entryPoint": path,
  "version": str,
  "description": str,
  "license": str,
  "urls": {
    "repository": str
  },
  "authors": [{
    "name": str, handle or username is ok
    "email": str, optionl
  }]
}
```

2. Now come up with metadata that will help find the plugin in a search interface:

Choose a set of categories:
  - "disassembly-and-processor-modules"
  - "file-parsers-and-loaders"
  - "decompilation"
  - "debugging-and-tracing"
  - "deobfuscation"
  - "collaboration-and-productivity"
  - "integration-with-third-parties-interoperability"
  - "api-scripting-and-automation"
  - "ui-ux-and-visualization"
  - "malware-analysis"
  - "vulnerability-research-and-exploit-development"
  - "other"


Come up with keywords that describe the plugin, its purpose, and related technologies.

3. Now propose an `ida-plugin.json` file with these changes.

4. Next develop a plan of action to migrate the plugin into the new hcli ecosystem.
Identify if this is a pure-Python plugin or a native plugin. Using the documentation, create a plan for any changes
that need to be made to the repo and its code structure so that it can be packaged into a plugin archive.
Try to minimize the number of changes you have to make. Show the plan and ask for confirmation.
Ultrathink about this step.

5. If needed, propose a GitHub Actions workflow that will build the native plugin, using the following as an example:
https://github.com/williballenthin/zydisinfo/blob/gha-hcli/.github/workflows/build.yml

6. Finally, update the readme to explain how to install the plugin using hcli (`hcli plugin install foo`).
An example:

```md
#### Using hcli (Recommended)
The easiest way to install Yarka is using the [Hex-Rays CLI tool (hcli)](https://github.com/HexRaysSA/ida-hcli):
```bash
pip install ida-hcli
hcli plugin install yarka
```

This will automatically install the plugin to your IDA user directory.

#### Manual Installation
Alternatively, you can manually install the plugin:
1. Copy `yarka.py` and the `yarka` folder to your IDA plugins directory
2. The plugins directory location depends on your system:
   - **Windows**: `%APPDATA%\Hex-Rays\IDA Pro\plugins\`
   - **macOS**: `~/Library/Application Support/IDA Pro/plugins/`
   - **Linux**: `~/.idapro/plugins/`
```

