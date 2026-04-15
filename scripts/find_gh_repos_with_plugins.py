import sys
import logging

from hcli.lib.ida.plugin.repo.github import find_github_repos_with_plugins


logging.basicConfig(level=logging.DEBUG)

for repo in sorted(find_github_repos_with_plugins(sys.argv[1])):
    print(repo)
