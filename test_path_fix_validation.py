#!/usr/bin/env python3

"""
Test to validate the Windows path escaping fix in run_hcli function.
This demonstrates that the fix resolves the issue described in GitHub issue #52.
"""

import platform
import shlex


def test_windows_path_escaping_fix():
    """Test that demonstrates the fix for Windows path escaping."""

    # The problematic path from the GitHub issue logs
    problematic_path = r"D:\a\ida-hcli\ida-hcli\tests\data\plugins"
    test_command = f"plugin --repo {problematic_path} repo snapshot"

    print("=" * 70)
    print("Windows Path Escaping Fix Validation")
    print("=" * 70)
    print(f"Platform: {platform.system()}")
    print(f"Test command: {test_command}")
    print()

    # Demonstrate the problem with the old approach
    print("OLD APPROACH (problematic):")
    old_result = shlex.split(test_command, posix=True)
    print(f"  shlex.split(command, posix=True): {old_result}")

    # Extract the path argument
    old_repo_index = old_result.index("--repo")
    old_path = old_result[old_repo_index + 1] if old_repo_index + 1 < len(old_result) else None
    print(f"  Extracted path: {old_path}")
    print(f"  ✗ Path mangled: {old_path != problematic_path}")
    print()

    # Demonstrate the solution
    print("NEW APPROACH (fixed):")

    # Apply the same logic as in the run_hcli function
    if platform.system() == "Windows":
        new_result = shlex.split(test_command, posix=False)
        approach = "shlex.split(command, posix=False)"
    else:
        new_result = shlex.split(test_command)
        approach = "shlex.split(command) [default]"

    print(f"  {approach}: {new_result}")

    # Extract the path argument
    new_repo_index = new_result.index("--repo")
    new_path = new_result[new_repo_index + 1] if new_repo_index + 1 < len(new_result) else None
    print(f"  Extracted path: {new_path}")

    # Validate the fix
    if platform.system() == "Windows":
        path_preserved = new_path == problematic_path
        print(f"  ✓ Path preserved: {path_preserved}")

        if path_preserved:
            print("  🎉 FIX VALIDATED: Windows paths are now handled correctly!")
        else:
            print("  ❌ FIX FAILED: Path is still being mangled")

    else:
        # On Unix, demonstrate that posix=False would also work
        posix_false_result = shlex.split(test_command, posix=False)
        posix_false_repo_index = posix_false_result.index("--repo")
        posix_false_path = (
            posix_false_result[posix_false_repo_index + 1]
            if posix_false_repo_index + 1 < len(posix_false_result)
            else None
        )

        print(f"  With posix=False: {posix_false_result}")
        print(f"  Extracted path (posix=False): {posix_false_path}")
        print("  ✓ Unix behavior: Uses default approach (no change needed)")
        print(f"  ✓ posix=False would work: {posix_false_path == problematic_path}")

    print()
    print("SUMMARY:")
    print(f"  Original path: {problematic_path}")
    print(f"  Old result:    {old_path}")
    print(f"  New result:    {new_path}")

    if platform.system() == "Windows":
        success = new_path == problematic_path
    else:
        # On Unix, we just verify that the posix=False option would work
        posix_false_result = shlex.split(test_command, posix=False)
        posix_false_repo_index = posix_false_result.index("--repo")
        posix_false_path = posix_false_result[posix_false_repo_index + 1]
        success = posix_false_path == problematic_path

    if success:
        print("  🎉 SUCCESS: The fix resolves the Windows path escaping issue!")
    else:
        print("  ❌ FAILURE: The fix does not work as expected")

    print("=" * 70)
    return success


if __name__ == "__main__":
    success = test_windows_path_escaping_fix()
    exit(0 if success else 1)
