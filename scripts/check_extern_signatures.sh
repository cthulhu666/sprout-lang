#!/usr/bin/env bash
# check_extern_signatures.sh — one C symbol, one Sprout `extern fn` declaration.
#
# An `extern fn` is invisible to the module system: `bundler.add_decl_to_symbols`
# ends with `| ast.ExternFnDecl _ _ _ _ _ -> acc` (bundler.sprout:597), so extern
# names are never module-qualified and two declarations of the same C symbol
# never collide. `ir_lowering.lower_extern_decls` then dedupes the emitted LLVM
# `declare` by NAME alone (ir_lowering.sprout:585).
#
# The consequence: when one symbol is declared twice with different parameter
# orders, both typecheck, a single `declare` is emitted, and whichever
# declaration a call site resolves against silently decides the argument order.
# Nothing in the pipeline reports it.
#
# That is not hypothetical. `regex_replace_all_literal` was declared
# (s, pattern, replacement) in the prelude and (pattern, replacement, text) in
# stdlib/regex.sprout. The C is (pattern, replacement, text) — the prelude copy
# was wrong, all three parameters are String, and it typechecked for months.
#
# The invariant enforced here is the stronger and simpler one: a symbol is
# declared exactly once, in the module that owns it. Consumers reach it by
# importing that module, which is sufficient because externs resolve by bare
# name once their module is in the bundle.

set -euo pipefail

cd "$(dirname "$0")/.."

decls=$(find stdlib -name '*.sprout' -print0 \
  | xargs -0 perl -ne '
      if (/^\s*(?:export\s+)?extern\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\(.*?)\s*$/) {
        my ($name, $sig) = ($1, $2);
        $sig =~ s/\s+/ /g;
        print "$name\t$ARGV:$.\t$sig\n";
      }
      close ARGV if eof;
    ' \
  | sort)

report=$(printf '%s\n' "$decls" | awk -F'\t' '
  { n = $1; count[n]++; if (!(n in seen)) { seen[n] = 1; order[++k] = n }
    detail[n] = detail[n] sprintf("      %s\n          %s\n", $2, $3) }
  END { for (i = 1; i <= k; i++) { n = order[i]
          if (count[n] > 1) printf "  %s  (%d declarations)\n%s\n", n, count[n], detail[n] } }
')

if [[ -n "$report" ]]; then
  echo "check-extern-signatures: these C symbols are declared more than once." >&2
  echo >&2
  printf '%s\n' "$report" >&2
  echo "  Externs are never module-scoped (bundler.sprout:597) and the LLVM declare is" >&2
  echo "  deduped by name (ir_lowering.sprout:585), so every copy is live and whichever" >&2
  echo "  one a call site resolves against decides the argument order — with no" >&2
  echo "  diagnostic when the arities match." >&2
  echo >&2
  echo "  Fix: keep ONE declaration, in the module that owns the symbol, verified against" >&2
  echo "  its C definition in runtime/sprout_runtime.c. Consumers import that module." >&2
  exit 1
fi

echo "==> check-extern-signatures ✓"
