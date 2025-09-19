import os

from hcli.lib.ida.plugin.repo.github import find_github_repos_with_plugins

for repo in sorted(find_github_repos_with_plugins(os.environ["GITHUB_TOKEN"])):
    print(repo)
