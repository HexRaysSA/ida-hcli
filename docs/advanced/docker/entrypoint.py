#!/usr/bin/env python3
import sys

USAGE = """usage: entrypoint.py BINARY

Open BINARY with idalib and print the address and name of each function."""


def main(args: list[str]) -> int:
    if args in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(args) != 1:
        print(USAGE, file=sys.stderr)
        return 2

    # Import after parsing the arguments, so --help does not load idalib.
    from ida_domain import Database

    with Database() as db:
        if not db.open(args[0]):
            print(f"error: could not open {args[0]}", file=sys.stderr)
            return 1
        for func in db.functions:
            print(f"{func.start_ea:#x}: {func.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
