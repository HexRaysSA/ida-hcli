import sys

import idapro
from ida_domain import Database
from ida_domain.database import IdaCommandOptions

idapro.enable_console_messages(True)

with Database.open(sys.argv[1], IdaCommandOptions(auto_analysis=True, new_database=True)) as db:
    assert len(db.functions) == 14
