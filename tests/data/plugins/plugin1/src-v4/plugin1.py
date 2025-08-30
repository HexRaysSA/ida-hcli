# /// script
# dependencies = [
#   "packaging>=25.0",
#   "rich>=13.0.0",
# ]
# ///

import ida_idaapi


class hello_plugmod_t(ida_idaapi.plugmod_t):
    def run(self, arg):
        print("Hello world from Python with inline dependencies!")
        return 0


class hello_plugin_t(ida_idaapi.plugin_t):
    flags = ida_idaapi.PLUGIN_UNL | ida_idaapi.PLUGIN_MULTI
    comment = "This is an example Python plugin with PEP 723 inline dependencies (v4.0.0)"
    help = "This is an example Python plugin with inline dependencies"
    wanted_name = "Example Python plugin (inline deps)"
    wanted_hotkey = ""

    def init(self):
        return hello_plugmod_t()


def PLUGIN_ENTRY():
    return hello_plugin_t()