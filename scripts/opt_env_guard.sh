#!/usr/bin/env bash
# Refuses to run while SPROUT_OPT_OFF is set: a committed seed must be
# what a plain build emits, not one built with a pass off.
set -uo pipefail
if [ -n "${SPROUT_OPT_OFF:-}" ]; then
  echo "opt_env_guard: SPROUT_OPT_OFF is set ('$SPROUT_OPT_OFF'); unset it, the seed must come from a plain build" >&2
  exit 1
fi
exit 0
