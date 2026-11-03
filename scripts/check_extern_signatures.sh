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
#
# One exception: stdlib/linvec.sprout redeclares `vector_mutset`, `vector_push` and
# `vector_truncate` with stdlib/mutable.sprout's exact signatures. Both are private,
# and linvec needs its own so a caller who imports `stdlib.mutable` still cannot write
# through a borrowed `Vector` (docs/unsafe-primitives-v0.md §4.3). Allowed only as that
# one identical pair: exactly one declaration in each of those two files.

set -euo pipefail

cd "${SPROUT_EXTERN_CHECK_ROOT:-$(dirname "$0")/..}"
status=0

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
  BEGIN { allowed["vector_mutset"] = 1; allowed["vector_push"] = 1; allowed["vector_truncate"] = 1 }
  { n = $1; count[n]++; if (!(n in seen)) { seen[n] = 1; order[++k] = n; first_sig[n] = $3 }
    split($2, loc, ":")
    if ($3 != first_sig[n]) differs[n] = 1
    if (loc[1] == "stdlib/mutable.sprout") inmut[n]++
    else if (loc[1] == "stdlib/linvec.sprout") inlin[n]++
    else elsewhere[n] = 1
    detail[n] = detail[n] sprintf("      %s\n          %s\n", $2, $3) }
  END { for (i = 1; i <= k; i++) { n = order[i]
          if ((n in allowed) && count[n] == 2 && inmut[n] == 1 && inlin[n] == 1 && !(n in differs) && !(n in elsewhere)) continue
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
  status=1
fi

# Premise 1 of docs/pointer-free-vectors-v0.md §4.5: a NOSCAN vector's elements are never
# scanned, so an extern whose declared type lies is a use-after-free no debug mode sees.
#
# Rule A: an extern whose signature names Vector/Vec/MutVec/LinVec must be on this list. An
# entry claims the C side never stores a heap pointer into a vector whose element type is a
# non-heap scalar, nor reinterprets one vector type as another (§4.5).
# Rule B: an extern declared `-> Int|Double|Char|Bool` (effect row ignored) whose C definition
# in runtime/*.c returns sprout_ptr_word(...) or sprout_str_word(...) returns a heap pointer
# typed as a scalar. An extern with no C definition (a compiler-lowered builtin) is skipped;
# SPROUT_EXTERN_CHECK_VERBOSE=1 lists them on stderr.
export EXT_ALLOW="
stdlib/prelude.sprout:vector_empty
stdlib/prelude.sprout:vector_length
stdlib/prelude.sprout:vector_get
stdlib/prelude.sprout:vector_set
stdlib/prelude.sprout:vector_append
stdlib/prelude.sprout:vector_concat
stdlib/prelude.sprout:vector_from_list
stdlib/mutable.sprout:vec_make_filled
stdlib/mutable.sprout:vector_mutset
stdlib/mutable.sprout:vector_get_direct
stdlib/mutable.sprout:vector_push
stdlib/mutable.sprout:vector_truncate
stdlib/linvec.sprout:vector_mutset
stdlib/linvec.sprout:vector_push
stdlib/linvec.sprout:vector_truncate
stdlib/process.sprout:proc_run_raw
stdlib/compiler.sprout:analysis_session_eval
stdlib/compiler.sprout:analysis_session_diagnostics
"
export EXT_VERBOSE="${SPROUT_EXTERN_CHECK_VERBOSE:-}"

roots=()
for d in stdlib ide examples; do
  if [[ -d "$d" ]]; then roots+=("$d"); fi
done
shopt -s nullglob
EXT_CSRC="$(printf '%s\n' runtime/*.c)"
shopt -u nullglob
export EXT_CSRC

premise_report=""
if ((${#roots[@]})); then
  premise_report=$(find "${roots[@]}" -name '*.sprout' -print0 | xargs -0 perl -e '
    my %allow = map { $_ => 1 } grep { length } split /\n/, $ENV{EXT_ALLOW};
    my $clean = "";
    for my $f (grep { length } split /\n/, $ENV{EXT_CSRC}) {
      open(my $fh, "<", $f) or die "$f: $!";
      local $/;
      $clean .= <$fh> . "\n";
    }
    $clean =~ s{/\*.*?\*/}{ }gs;
    $clean =~ s{//[^\n]*}{ }g;
    $clean =~ s{"(?:\\.|[^"\\\n])*"}{""}g;
    $clean =~ s{\x27(?:\\.|[^\x27\\\n])*\x27}{\x27\x27}g;
    # The body of file-scope C function $n, or undef when none is defined.
    sub body {
      my $n = shift;
      $clean =~ /^[A-Za-z_][^\n;{}()]*\b\Q$n\E\s*\([^;{}]*\)\s*\{/m or return undef;
      my $start = $+[0];
      my ($i, $depth) = ($start, 1);
      while ($depth > 0 && $i < length $clean) {
        my $ch = substr($clean, $i++, 1);
        if ($ch eq "{") { $depth++ } elsif ($ch eq "}") { $depth-- }
      }
      return substr($clean, $start, $i - $start);
    }
    my @nodef;
    while (<>) {
      if (/^\s*(?:export\s+)?extern\s+fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\(.*?)\s*$/) {
        my ($name, $sig) = ($1, $2);
        $sig =~ s/\s+/ /g;
        if ($sig =~ /\b(?:Vector|Vec|MutVec|LinVec)\b/ && !$allow{"$ARGV:$name"}) {
          print "A\t$ARGV:$.\t$name\t$sig\n";
        }
        if ($sig =~ /->\s*(?:Int|Double|Char|Bool)\s*(?:!\s*\{[^}]*\})?\s*$/) {
          my $b = body($name);
          if (!defined $b) { push @nodef, $name }
          elsif ($b =~ /\breturn\s+(?:\([^)]*\)\s*)?(sprout_(?:ptr|str)_word)\s*\(/) {
            print "B\t$ARGV:$.\t$name\t$sig\t$1\n";
          }
        }
      }
      close ARGV if eof;
    }
    print STDERR "no C definition: $_\n" for ($ENV{EXT_VERBOSE} ? @nodef : ());
    ' | sort)
fi

report_a=$(printf '%s\n' "$premise_report" | awk -F'\t' '$1 == "A" { printf "  %s  %s\n      %s\n", $3, $2, $4 }')
report_b=$(printf '%s\n' "$premise_report" | awk -F'\t' '$1 == "B" { printf "  %s  %s\n      %s\n      C body returns %s(...)\n", $3, $2, $4, $5 }')

if [[ -n "$report_a" ]]; then
  echo "check-extern-signatures: these externs take or return a vector type and are not on the allowlist." >&2
  echo >&2
  printf '%s\n' "$report_a" >&2
  echo "  A NOSCAN vector's elements are never scanned (docs/pointer-free-vectors-v0.md §4.5," >&2
  echo "  premise 1). Verify the C side never stores a heap pointer into a vector whose element" >&2
  echo "  type is a non-heap scalar, nor reinterprets one vector type as another; then add" >&2
  echo "  file:name to EXT_ALLOW in scripts/check_extern_signatures.sh." >&2
  echo >&2
  status=1
fi

if [[ -n "$report_b" ]]; then
  echo "check-extern-signatures: these externs are declared to return a scalar but their C body returns a heap pointer." >&2
  echo >&2
  printf '%s\n' "$report_b" >&2
  echo "  A heap pointer typed Int/Double/Char/Bool is invisible to the GC, and a NOSCAN" >&2
  echo "  vector would hold it unscanned (docs/pointer-free-vectors-v0.md §4.5, premise 1)." >&2
  echo "  Fix the declared return type." >&2
  echo >&2
  status=1
fi

if ((status != 0)); then exit 1; fi

echo "==> check-extern-signatures ✓"
