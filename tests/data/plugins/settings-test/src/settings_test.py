"""Test plugin that exercises settings API path handling.

This plugin is used by integration tests to verify that:
1. get_current_plugin() correctly detects the plugin name from the call stack
2. get_current_plugin_setting() can read settings
3. Path handling works correctly across platforms (especially Windows)

The plugin writes its results to a JSON file specified by the
SETTINGS_TEST_OUTPUT environment variable.
"""

import json
import os
import sys
import traceback

import ida_idaapi


def run_settings_test():
    """Run the settings API test and return results dict."""
    results = {
        "success": False,
        "plugin_name": None,
        "test_value": None,
        "test_bool": None,
        "errors": [],
        "call_stack_files": [],
    }

    try:
        import inspect

        frame = inspect.currentframe()
        while frame is not None:
            results["call_stack_files"].append(frame.f_code.co_filename)
            frame = frame.f_back
    except Exception as e:
        results["errors"].append(f"Failed to collect call stack: {e}")

    try:
        from hcli.lib.ida.plugin.settings import get_current_plugin

        plugin_name = get_current_plugin()
        results["plugin_name"] = plugin_name
    except Exception as e:
        results["errors"].append(f"get_current_plugin failed: {e}\n{traceback.format_exc()}")
        return results

    try:
        from hcli.lib.ida.plugin.settings import get_current_plugin_setting

        test_value = get_current_plugin_setting("test_value")
        results["test_value"] = test_value
    except Exception as e:
        results["errors"].append(f"get_current_plugin_setting(test_value) failed: {e}\n{traceback.format_exc()}")

    try:
        from hcli.lib.ida.plugin.settings import get_current_plugin_setting

        test_bool = get_current_plugin_setting("test_bool")
        results["test_bool"] = test_bool
    except Exception as e:
        results["errors"].append(f"get_current_plugin_setting(test_bool) failed: {e}\n{traceback.format_exc()}")

    if not results["errors"]:
        results["success"] = True

    return results


def write_results(results):
    """Write results to the output file specified by environment variable."""
    output_path = os.environ.get("SETTINGS_TEST_OUTPUT")
    if not output_path:
        print("[settings-test] ERROR: SETTINGS_TEST_OUTPUT not set, cannot write results")
        return

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"[settings-test] Results written to {output_path}")
    except Exception as e:
        print(f"[settings-test] ERROR: Failed to write results: {e}")


class settings_test_plugmod_t(ida_idaapi.plugmod_t):
    def run(self, arg):
        return 0


class settings_test_plugin_t(ida_idaapi.plugin_t):
    flags = ida_idaapi.PLUGIN_UNL
    comment = "Settings API test plugin"
    help = "Tests settings API path handling"
    wanted_name = "Settings Test"
    wanted_hotkey = ""

    def init(self):
        print("[settings-test] Plugin init() called")
        print(f"[settings-test] __file__ = {__file__}")
        print(f"[settings-test] sys.path = {sys.path}")

        results = run_settings_test()

        if results["success"]:
            print(f"[settings-test] SUCCESS: plugin_name={results['plugin_name']}")
            print(f"[settings-test] test_value={results['test_value']}")
            print(f"[settings-test] test_bool={results['test_bool']}")
        else:
            print(f"[settings-test] FAILED: {results['errors']}")

        write_results(results)

        return ida_idaapi.PLUGIN_SKIP


def PLUGIN_ENTRY():
    return settings_test_plugin_t()
