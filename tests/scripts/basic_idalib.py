import sys

import idapro
from ida_domain import Database
from ida_domain.database import IdaCommandOptions

idapro.enable_console_messages(True)

with Database.open(sys.argv[1], IdaCommandOptions(auto_analysis=True, new_database=True)) as db:
    assert len(db.functions) == 14

    # Test ida_lines.generate_disassembly compatibility across IDA versions.
    #
    # The `include_hidden` parameter was added in IDA 9.2:
    #   IDA 9.1: generate_disassembly(ea, max_lines, as_stack, notag)
    #   IDA 9.2: generate_disassembly(ea, max_lines, as_stack, notag, include_hidden=False)
    #
    # Always use 4 positional args to stay compatible with IDA 9.1+.
    # Pass `include_hidden` as a keyword argument only when needed.
    import ida_lines
    import ida_name
    from ida_idaapi import BADADDR

    main_ea = ida_name.get_name_ea(BADADDR, "_main")
    assert main_ea != BADADDR, "_main function not found"

    result = ida_lines.generate_disassembly(main_ea, 10, False, True)
    assert result is not None, "generate_disassembly returned None"
    lineno, lines = result
    assert len(lines) > 0, "generate_disassembly returned no lines"

    print(f"generate_disassembly at _main (0x{main_ea:x}), lineno={lineno}:")
    for i, line in enumerate(lines):
        print(f"  [{i}] {line!r}")
