#!/usr/bin/env python3
import sys
from ida_domain import Database

with Database() as db:
    if db.open(sys.argv[1]):
        for func in db.functions:
            print(f"{func.start_ea:#x}: {func.name}")
