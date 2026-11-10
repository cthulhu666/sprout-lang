#!/usr/bin/env bash
# Gate: second-root (`--package-root`) module resolution.
#
#   Positive: an app importing a module from an extra package root type-checks
#             cleanly when that root is registered with --package-root.
#   Negative: the same import with NO --package-root must fail to resolve
#             (guards against accidentally widening the default search path).
#
# Uses `--phase check` (resolve + typecheck) — proving the import resolves is the
# unit under test; end-to-end compile+run of a cross-package program is covered by
# the http_pg_server demo in the sprout-postgres repo.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRV="${SPROUT_STAGE1:-$ROOT/build/compile_driver_bin_stage1}"
STDLIB="$ROOT/stdlib"
FIX="$ROOT/tests/conformance/package_resolution"
PKG_ROOT="$FIX/roots"
APP="$FIX/app.spr"
fail=0

# Output is matched with `has` (here-strings), never `echo "$out" | grep -q`: see test_lib.sh.
# shellcheck source=scripts/test_lib.sh
source "$ROOT/scripts/test_lib.sh"

# Real diagnostics are `<pos>ERROR: ...` or `Unknown variable ...`; the check phase
# also dumps the type env (names like `result_map_error`), so match error markers
# precisely (case-sensitive `ERROR:` / `Unknown variable`), not a bare `error`.
errors() { grep -E 'ERROR:|Unknown variable' <<<"$1"; }

# Runs the stage-1 check phase on the given args; sets `out` (stdout+stderr) and `rc`.
# A crash or an output with no verdict is noted on stderr; it does not fail the gate.
check_out() {
  out="$("$DRV" --phase check "$STDLIB" "$@" 2>&1)"
  rc=$?
  if [ "$rc" -gt 1 ] || ! has "$out" -E '^OK$|ERROR:|Unknown variable'; then
    echo "NOTE check ended with no verdict (exit $rc): $*" >&2
    tail -5 <<<"$out" >&2
  fi
}

# Reports a failed check: the message and exit code, then the ERROR lines if there are
# any, otherwise the last 5 lines of output. Reads `out` and `rc`.
failed() {
  local errs
  echo "FAIL $1 (exit $rc)"
  errs="$(errors "$out" | head -5)"
  if [ -n "$errs" ]; then
    printf '%s\n' "$errs"
  else
    tail -5 <<<"$out"
  fi
  fail=1
}

# Judge `out`; <pass-msg> and <fail-msg> follow PASS and FAIL.
#   out_clean <pass-msg> <fail-msg>                      an `OK` line and no errors
#   out_has <-F|-G> <pass-msg> <fail-msg> <needle>...    every needle present (-F fixed, -G regex)
out_clean() {
  if has "$out" '^OK$' && ! errors "$out" >/dev/null; then echo "PASS $1"; else failed "$2"; fi
}
out_has() {
  local mode="$1" pass="$2" msg="$3" needle
  shift 3
  for needle in "$@"; do
    has "$out" "$mode" -e "$needle" || { failed "$msg"; return; }
  done
  echo "PASS $pass"
}

# Run a check, then judge it. `--` ends expect_has's needles (fixed strings unless -G).
#   expect_clean <pass-msg> <fail-msg> <check args...>
#   expect_has [-G] <pass-msg> <fail-msg> <needle>... -- <check args...>
expect_clean() {
  local pass="$1" msg="$2"
  shift 2
  check_out "$@"
  out_clean "$pass" "$msg"
}
expect_has() {
  local mode=-F pass msg needles=()
  if [ "$1" = -G ]; then mode=-G; shift; fi
  pass="$1" msg="$2"
  shift 2
  while [ "$1" != -- ]; do needles+=("$1"); shift; done
  shift
  check_out "$@"
  out_has "$mode" "$pass" "$msg" "${needles[@]}"
}

# Positive: registered extra root -> import resolves, check reports OK, no errors.
expect_clean "positive: second-root import resolved and type-checked" \
  "positive: --package-root did not resolve demo.greet" \
  --package-root "$PKG_ROOT" "$APP"

# Negative: no extra root -> the import of demo.greet is itself the error.
expect_has -G "negative: unregistered dotted import correctly failed to resolve" \
  "negative: expected an unknown-module error on the demo.greet import without --package-root" \
  'import demo.greet: unknown module' -- "$APP"

# Header check: an imported file must declare a `module` header
# (docs/prelude-scope-v0.md §4.2 step 3). The root IS registered here, so
# resolution succeeds and the header check is the only thing that can reject it.
# Asserted on the MESSAGE, not just the exit status: the whole point of the
# diagnostic is that it names the file and the cause, where the old behaviour
# reported `... is defined more than once in this module` about whichever symbol
# happened to collide with the prelude — or nothing at all.
expect_has -G "header: headerless imported file diagnosed by path and cause" \
  "header: expected a 'no \`module\` header' diagnostic naming headerless.sprout" \
  'no `module` header' 'headerless.sprout' \
  -- --package-root "$PKG_ROOT" "$FIX/app_headerless.spr"

# Class-name collision across roots: two modules declaring a class with the same
# SHORT name and different declared effects. Rule 8's instance/class effect check
# keys on the class, and a short-name-only key collapses the two — rejecting a
# legal program while naming a class that declares the opposite effect.
# Here because the shape needs two sibling modules, which needs a package root.
expect_clean "collision: same-short-named classes from two modules judged separately" \
  "collision: legal program rejected — class-keyed effect lookup collapsed two classes" \
  --package-root "$PKG_ROOT" "$FIX/app_class_name_collision.spr"

# The firing half of the same lookup. The assertion above is acceptance-only, so a
# key that never matches an imported class passes it; this one fails unless the
# effect declared by a class in ANOTHER module is found and enforced.
expect_has "cross-module reject: an imported class's declared effect is enforced" \
  "cross-module reject: expected a rule-8 mismatch naming demo.enc_quiet.Enc" \
  'of class `demo.enc_quiet.Enc`: declared !{IO}, but the class declares pure' \
  -- --package-root "$PKG_ROOT" "$FIX/app_cross_module_class_reject.spr"

# A wrap exported without `(..)` publishes its TYPE and not its constructor. This
# needs --package-root, so it has no home among the type_error fixtures; both halves
# live here. Construction and the destructor pattern resolve on different paths and
# report different diagnostics, so each is asserted separately — a gate on one alone
# would let an importer unwrap a value it cannot build.
#
# Rejecting it is half the job; saying WHY is the other half. Bare "Unknown
# variable" reads as a typo and sends the reader hunting for a missing
# declaration instead of a missing marker, so both halves assert the note too.
# A canonical name is not a spelling the reader may write (spec-v0 §3), so a
# diagnostic offering one would answer "how do I reach this?" with a rejected
# form. Name the module and the symbol separately.
# Judges `out` for one case: <case> <rejection-needle> <pass-msg> <fail-msg>.
wrap_opacity() {
  out_has -F "$3" "$4" "$2"
  out_has -F "wrap opacity ($1): the diagnostic names the module and the marker" \
    "wrap opacity ($1): expected a note naming demo.sealed and \`(..)\`" \
    'declares `Sealed`, which is not in scope here' 'exported only with `(..)`'
  if has "$out" -F 'demo.sealed.Sealed'; then
    failed "wrap opacity ($1): diagnostic leaks the canonical name"
  fi
}

check_out --package-root "$PKG_ROOT" "$FIX/app_wrap_ctor_construct.spr"
wrap_opacity construct 'Unknown variable: sealed.Sealed' \
  "wrap opacity: an abstract wrap's constructor is unreachable cross-module" \
  "wrap opacity: expected 'Unknown variable: sealed.Sealed'"

check_out --package-root "$PKG_ROOT" "$FIX/app_wrap_ctor_pattern.spr"
wrap_opacity pattern 'Unknown constructor: sealed.Sealed' \
  "wrap opacity: the destructor pattern is hidden with the constructor" \
  "wrap opacity: expected 'Unknown constructor: sealed.Sealed'"

# A record exported without `(..)` publishes its type and not its representation.
# The two halves are settled by DIFFERENT components — construction by the bundler
# (the name is never bound), field access by inference (the name resolved; the
# type is what is private) — so one passing says nothing about the other.
expect_has "record opacity: an abstract record cannot be constructed cross-module" \
  "record opacity: expected recop.Opaque construction rejected, with the \`(..)\` note" \
  'Unknown record type or field: recop.Opaque' 'exported only with `(..)`' \
  -- --package-root "$PKG_ROOT" "$FIX/app_record_construct.spr"

expect_has "record opacity: an abstract record's field is unreadable cross-module" \
  "record opacity: expected a field read of demo.recop.Opaque to be refused" \
  'is abstract here' 'demo.recop' \
  -- --package-root "$PKG_ROOT" "$FIX/app_record_field.spr"

# A `wrap linear` is linear in its importer too, and an abstract one stays shut: its
# field is not linear, so unwrapping it would let an importer copy what it holds.
expect_has "linear wrap: an importer sees the wrap as linear" \
  "linear wrap: expected the unspent demo.linwrap.Token to be a leak" \
  "linear value 't' is never used" \
  -- --package-root "$PKG_ROOT" "$FIX/app_linear_wrap_leak.spr"

expect_has "linear wrap: an abstract linear wrap cannot be unwrapped cross-module" \
  "linear wrap: expected 'Unknown constructor: linwrap.Token'" \
  'Unknown constructor: linwrap.Token' \
  -- --package-root "$PKG_ROOT" "$FIX/app_linear_wrap_pattern.spr"

# A published linear type is allowed when every field is linear, and refused in
# the module that declares it when one is not (docs/linear-publish-v0.md §4).
expect_clean "linear publish: an importer destructures a published all-linear Pair" \
  "linear publish: expected demo.linpub.Pair to check clean" \
  --package-root "$PKG_ROOT" "$FIX/app_linear_publish_ok.spr"

expect_has "linear publish: a published linear wrap over Int is refused at its module" \
  "linear publish: expected demo.linpub_bad.Ticket to be refused" \
  '`Ticket` is linear, and `(..)` publishes its constructor, but its field `Int` is not linear' \
  -- --package-root "$PKG_ROOT" "$FIX/app_linear_publish_bad.spr"

# The batch CLI's half of the canonical-name rule. The env front end has its own
# (tests/stdlib/compiler/test_canonical_name_rejected.spr); the two have diverged
# before over package roots, which is why neither stands in for the other.
expect_has "canonical name: a private binding is unreachable by its bundled name" \
  "canonical name: expected demo.dup_one.hidden_helper to be rejected" \
  'is a canonical name, not a way to reach a module' \
  -- --package-root "$PKG_ROOT" "$FIX/app_canonical_name.spr"

# The one-constructor clash (#422) reads the prelude's types as in scope everywhere.
# A `no_prelude` entry has the prelude's empty module name, so its own types must
# not count for a module it imports. Needs a second module, hence here.
expect_clean "no_prelude: an entry's own type does not clash with an imported constructor" \
  "no_prelude: demo.tokbar's constructor \`Token\` was judged against the entry's types" \
  --package-root "$PKG_ROOT" "$FIX/app_no_prelude_ctor_clash.spr"

# `no_prelude` is a header line, so an import after it is still read.
expect_clean "no_prelude: an import after the directive resolves" \
  "no_prelude: the import after \`no_prelude\` was dropped" \
  --package-root "$PKG_ROOT" "$FIX/app_no_prelude_then_import.spr"

# A clash inside an IMPORTED module is still reported, but without its position: a
# line:col from demo.clashdep would be read against the entry file.
expect_has -G "dependency clash: reported by module, with no position from the other file" \
  "dependency clash: expected a position-less error naming demo.clashdep.Bad" \
  '^ERROR: bundle: type `demo.clashdep.Bad` has one constructor' \
  -- --package-root "$PKG_ROOT" "$FIX/app_dep_ctor_clash.spr"

# An extern whose signature WRITES `local` anywhere is private to its module, not only
# one with a top-level `local` parameter: `local` inside the return type or a
# parameter's type lends a borrowed value to a closure the C body may keep.
for shape in return param_type; do
  expect_has "private extern ($shape): a nested \`local\` makes the extern private to its module" \
    "private extern ($shape): expected the extern to be rejected as private to demo.locext" \
    'is private to `demo.locext`' \
    -- --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_in_$shape.spr"
done

# `local` reached through a type alias counts: in the extern's own module, and from
# another module through a chain of two aliases.
for shape in alias imported_alias; do
  expect_has "private extern ($shape): \`local\` behind an alias makes the extern private" \
    "private extern ($shape): expected the extern to be rejected as private" \
    'is private to `demo.alias' \
    -- --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_via_$shape.spr"
done

# ...and through any type definition the signature names: a sum's constructor
# field, a record field, a wrap. The recursive type has no `local`, so its extern
# stays public: that guards the cycle check and against over-marking.
for shape in adt record wrap; do
  expect_has "private extern ($shape): \`local\` in a type definition makes the extern private" \
    "private extern ($shape): expected the extern to be rejected as private to demo.adtext" \
    'is private to `demo.adtext`' \
    -- --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_in_$shape.spr"
done

expect_clean "private extern: a recursive type with no \`local\` leaves its extern public" \
  "private extern: an extern naming a recursive type with no \`local\` was rejected" \
  --package-root "$PKG_ROOT" "$FIX/app_extern_recursive_type_stays_public.spr"

# Two declarations of one extern that differ only in variable names agree.
expect_clean "extern redeclaration: renamed type and effect variables are the same signature" \
  "extern redeclaration: renamed variables were judged a different signature" \
  --package-root "$PKG_ROOT" "$FIX/app_extern_redeclare_renamed_vars.spr"

# An extern is private to its module unless that module writes `export extern fn`
# (docs/extern-visibility-v0.md). The `local` cases above are the special case: those
# stay private whether or not `export` is written, and `export` on one is an error.
expect_has "extern visibility: an unexported extern is private to its module" \
  "extern visibility: expected __priv_add to be rejected as private to demo.privext" \
  '`__priv_add` is private to `demo.privext`. Mark it `export extern fn` there, or declare it in this module' \
  -- --package-root "$PKG_ROOT" "$FIX/app_private_extern_general_reached.spr"

# A headerless `no_prelude` entry has module name "" but is not the prelude: its own
# extern declaration must not export the name to another module.
expect_has "extern visibility: a no_prelude entry's extern stays private" \
  "extern visibility: a no_prelude entry's extern was exported to another module" \
  '`__priv_add` is private to' \
  -- --package-root "$PKG_ROOT" "$FIX/app_no_prelude_entry_extern_stays_private.spr"

for shape in own_and_exported redeclared; do
  expect_clean "extern visibility ($shape): accepted" \
    "extern visibility ($shape): a legal use of an extern was rejected" \
    --package-root "$PKG_ROOT" "$FIX/app_private_extern_$shape.spr"
done

# Exportedness is per NAME across the build: one module's private redeclaration must
# not hide another module's exported extern from a third. Both import orders, so the
# private declaration is last in one bundle and first in the other.
for order in ab ba; do
  expect_clean "extern visibility (collision $order): a private redeclaration does not hide an exported extern" \
    "extern visibility (collision $order): the exported extern was hidden by a private redeclaration" \
    --package-root "$PKG_ROOT" "$FIX/app_extern_collision_$order.spr"
done

# The prelude's externs and the `no_prelude` floor's are exported implicitly.
for shape in prelude_extern_reachable no_prelude_floor_extern; do
  expect_clean "extern visibility ($shape): accepted" \
    "extern visibility ($shape): a prelude or floor extern was rejected" \
    --package-root "$PKG_ROOT" "$FIX/app_$shape.spr"
done

expect_has "extern visibility: \`export\` on an extern that writes \`local\` is an error" \
  "extern visibility: expected \`export\` on a \`local\` extern to be rejected" \
  '`export` on extern `__bad_export_local` has no effect: its signature writes `local`, so it is reachable only from `demo.exportlocal`. Remove `export`' \
  -- --package-root "$PKG_ROOT" "$FIX/app_export_extern_local.spr"

# An importer's instance is not in scope in the module it imports, which is
# checked first. Two halves: a method call, and a `try` whose instance is fusable.
expect_has "instance scope: an importer's instance does not reach the module it imports" \
  "instance scope: expected no ToString instance in demo.orphan_show.describe" \
  'No instance of ToString for demo.orphan_show.Foo in function demo.orphan_show.describe' \
  -- --package-root "$PKG_ROOT" "$FIX/app_importer_instance_not_in_import.spr"

expect_has "instance scope: an importer's Propagate instance does not fuse a \`try\` it imports" \
  "instance scope: expected no Propagate instance in demo.orphan_try.bump" \
  '`demo.orphan_try.Fetch Int` has no `Propagate` instance in function demo.orphan_try.bump' \
  -- --package-root "$PKG_ROOT" "$FIX/app_importer_propagate_not_in_import.spr"

# A method is reached as a value is (docs/qualified-class-methods-v0.md, rule 3):
# a module bundled but not imported gives no method, and a class import binds its
# methods, so one beside a function import of the same name is bound twice.
expect_has "method scope: a method of a module not imported here is unknown" \
  "method scope: expected 'Unknown variable: tally'" \
  'Unknown variable: tally' \
  -- --package-root "$PKG_ROOT" "$FIX/app_method_not_imported.spr"

expect_has "method scope: a class import binds its methods" \
  "method scope: expected \`tally\` imported twice" \
  '`tally` is imported twice in this file' \
  -- --package-root "$PKG_ROOT" "$FIX/app_class_import_method_conflict.spr"

expect_has "method scope: importing a hidden class's method names the class" \
  "method scope: expected the diagnostic to name class \`Hidden\`" \
  '`peek` is a method of class `Hidden`, which is not exported' \
  -- --package-root "$PKG_ROOT" "$FIX/app_import_hidden_method.spr"

# A record's constructor is named after its type, so an abstract record and a sum
# constructor of the same name are one constructor declared twice. Rejected in the
# declaring module; without the rule the sum's exported `Token` bound to the same
# canonical name as the abstract record's constructor, and an importer could build
# the record directly.
expect_has "record ctor clash: an abstract record sharing a sum constructor's name is rejected" \
  "record ctor clash: expected \`demo.recforge.Token\` is defined more than once in this module" \
  '`demo.recforge.Token` is defined more than once in this module' \
  -- --package-root "$PKG_ROOT" "$FIX/app_record_forge_via_ctor.spr"

[ "$fail" -eq 0 ] && echo "==> package-resolution gate: OK" || echo "==> package-resolution gate: FAILED"
exit $fail
