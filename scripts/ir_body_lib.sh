# shellcheck shell=bash
# Shared helper for scripts that read emitted LLVM IR. Sourced, never executed.
# Sourced by: spec_gate.sh, noscan_gate.sh, linvec_smoke.sh.

# <ll file> <symbol> -> the text of that function's define, through its closing brace. The
# symbol (no `@`) is matched as a literal prefix up to `(`: copy names hold `$` and `.`, which
# a regex would eat.
ir_fn_body() {
  awk -v pre="@$2(" 'index($0, "define ") == 1 && index($0, pre) { on = 1 } on { print } on && /^}/ { exit }' "$1"
}
