#!/usr/bin/env bash
# Refuses to run while SPROUT_OPT_OFF or SPROUT_OPT_ON is set: a committed seed must be
# what a plain build emits, not one built with a pass forced on or another off.
set -uo pipefail
for v in SPROUT_OPT_OFF SPROUT_OPT_ON; do
  if [ -n "${!v:-}" ]; then
    echo "opt_env_guard: $v is set ('${!v}'); unset it, the seed must come from a plain build" >&2
    exit 1
  fi
done
exit 0
