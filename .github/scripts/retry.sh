#!/usr/bin/env bash
# Retry a command with linear backoff to ride out transient failures of the IDA
# download backend (e.g. HTTP 502 / "no files downloaded") that intermittently
# break the CI "Install IDA" steps. Keeps the command (and any secrets it carries)
# inside our own code rather than handing it to a third-party retry action.
#
# Usage: retry.sh <max_attempts> <base_sleep_seconds> <command> [args...]
#   sleeps base, 2*base, 3*base, ... between attempts.
set -uo pipefail

max="$1"
base="$2"
shift 2

attempt=1
while true; do
  "$@" && exit 0
  status=$?
  if [ "$attempt" -ge "$max" ]; then
    # Note: only the program name is printed, never the args, to avoid echoing secrets.
    echo "::error::'$1' still failing after ${max} attempts (last exit ${status})."
    exit "$status"
  fi
  wait=$((attempt * base))
  echo "::warning::Attempt ${attempt}/${max} failed (exit ${status}); retrying in ${wait}s..."
  sleep "$wait"
  attempt=$((attempt + 1))
done
