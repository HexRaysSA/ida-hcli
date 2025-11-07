# Hex-Rays guide for suggesting changes

1. navigate to the GitHub repo
2. fork to "HexRays-plugin-contributions" organization, using the defaults
3. check it out locally, like `git clone git@github.com:HexRays-plugin-contributions/foo.git`
4. `cd foo`
5. `git checkout -b ida-plugin-json`
6. add `ida-plugin.json` and any other packaging changes. Commit and push them. This will be submitted upstream as a PR.

      1. look at `plugins-AGENT.md` for some ideas
      1. `.plugin.urls.repo` should be the upstream repo
      1. `.plugin.authors` should be the original author
      1. `hcli plugin lint /path/to/plugin` can help identify issues
      1. if its pure Python, then the default release source archive is likely sufficient
      1. if its a native plugin, you'll need to figure out how to build on GitHub Actions. this might take some time
      1. pay special attention to the two areas of complexity:
        1. Python (or other) dependencies
        1. configuration/settings

7. `git checkout -b hr-test-release`
8. update `ida-plugin.json` so that this fork can temporarily be used by the plugin repository. commit and push them.
      1. set `.plugin.urls.repo` to the fork, like `https://github.com/HexRays-plugin-contributions/foo`
      1. add `.plugin.maintainers` entry for yourself
9. create a release using the `hr-test-release` branch
10. ensure the plugin can be installed: `hcli plugin install https://.../url/to/release/zip`

## Example: Packaging eset/DelphiHelper

Because this is a pure-Python plugin, packaging was very easy! Just add the metadata file and do releases on GitHub:

1. added `ida-plugin.json`: [PR#5](https://github.com/eset/DelphiHelper/pull/5/files)
2. asked to start using GitHub Releases via the web interface
3. ...done


## Example: Packaging milankovo/zydishelper

Because this is a plugin written in C++ and compiled to a native shared object, packaging took a little more work:

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
