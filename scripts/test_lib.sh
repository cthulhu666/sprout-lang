# shellcheck shell=bash
# Shared helpers for the test runners in the justfile and the golden-IR scripts. Sourced, never executed.

# <seconds> <command...>: run with a wall-clock limit; exit status 142 means it timed out.
# `or exit 127`: without it a failed exec lets perl exit 0, and a missing binary reads as success.
limited() {
  local secs="$1"; shift
  perl -e 'alarm shift; exec @ARGV or exit 127' "$secs" "$@"
}

# <text> [grep-flags...] <pattern>: quiet grep over a string, matched via a here-string.
# `echo "$text" | grep -q` fails under pipefail: grep exits on its first match, the writer gets SIGPIPE.
has() { local text="$1"; shift; grep -q "$@" <<<"$text"; }

# [default-seconds]: echo the per-step limit, SPROUT_TEST_TIMEOUT or the default (300).
# Only a positive integer is valid: `0` disables the alarm and `5m` would become 5 s.
test_timeout() {
  local v="${SPROUT_TEST_TIMEOUT:-${1:-300}}"
  if [[ ! "$v" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: SPROUT_TEST_TIMEOUT must be a positive integer of seconds (got '$v')" >&2
    return 1
  fi
  echo "$v"
}

NO_VERDICT_MSG='NO VERDICT: suite printed no "SUITE PASSED" line (did main call summary/run_suite?)'

# [file]: judge a suite's output (stdin when no file) that already exited 0.
# Returns 0 for a pass (a SUITE PASSED line, no SUITE FAILED line), 1 for FAILED, 2 for no verdict.
suite_verdict() {
  local text
  text="$(cat "${1:--}")"
  if has "$text" "^SUITE FAILED"; then return 1; fi
  has "$text" "^SUITE PASSED$" || return 2
}
