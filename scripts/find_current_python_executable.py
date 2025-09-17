import logging

import hcli.lib.ida.python

logging.basicConfig(level=logging.DEBUG)


print(hcli.lib.ida.python.find_current_python_executable())
