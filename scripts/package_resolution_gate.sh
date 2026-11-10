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

# Real diagnostics are `<pos>ERROR: ...` or `Unknown variable ...`; the check phase
# also dumps the type env (names like `result_map_error`), so match error markers
# precisely (case-sensitive `ERROR:` / `Unknown variable`), not a bare `error`.
errors() { grep -E 'ERROR:|Unknown variable' <<<"$1"; }

# Output is matched with here-strings, never `echo "$out" | grep -q`: `grep -q` exits on
# its first match, and under pipefail the writer's SIGPIPE then fails the pipeline.

# Runs the stage-1 check phase on the given args; sets `out` (stdout+stderr) and `rc`.
check_out() {
  out="$("$DRV" --phase check "$STDLIB" "$@" 2>&1)"
  rc=$?
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

# Positive: registered extra root -> import resolves, check reports OK, no errors.
check_out --package-root "$PKG_ROOT" "$APP"
if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
  echo "PASS positive: second-root import resolved and type-checked"
else
  failed "positive: --package-root did not resolve demo.greet"
fi

# Negative: no extra root -> the import of demo.greet is itself the error.
check_out "$APP"
if grep -q 'import demo.greet: unknown module' <<<"$out"; then
  echo "PASS negative: unregistered dotted import correctly failed to resolve"
else
  failed "negative: expected an unknown-module error on the demo.greet import without --package-root"
fi

# Header check: an imported file must declare a `module` header
# (docs/prelude-scope-v0.md §4.2 step 3). The root IS registered here, so
# resolution succeeds and the header check is the only thing that can reject it.
# Asserted on the MESSAGE, not just the exit status: the whole point of the
# diagnostic is that it names the file and the cause, where the old behaviour
# reported `... is defined more than once in this module` about whichever symbol
# happened to collide with the prelude — or nothing at all.
check_out --package-root "$PKG_ROOT" "$FIX/app_headerless.spr"
if grep -q 'no `module` header' <<<"$out" && grep -q 'headerless.sprout' <<<"$out"; then
  echo "PASS header: headerless imported file diagnosed by path and cause"
else
  failed "header: expected a 'no \`module\` header' diagnostic naming headerless.sprout"
fi

# Class-name collision across roots: two modules declaring a class with the same
# SHORT name and different declared effects. Rule 8's instance/class effect check
# keys on the class, and a short-name-only key collapses the two — rejecting a
# legal program while naming a class that declares the opposite effect.
# Here because the shape needs two sibling modules, which needs a package root.
check_out --package-root "$PKG_ROOT" "$FIX/app_class_name_collision.spr"
if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
  echo "PASS collision: same-short-named classes from two modules judged separately"
else
  failed "collision: legal program rejected — class-keyed effect lookup collapsed two classes"
fi

# The firing half of the same lookup. The assertion above is acceptance-only, so a
# key that never matches an imported class passes it; this one fails unless the
# effect declared by a class in ANOTHER module is found and enforced.
check_out --package-root "$PKG_ROOT" "$FIX/app_cross_module_class_reject.spr"
if grep -qF 'of class `demo.enc_quiet.Enc`: declared !{IO}, but the class declares pure' <<<"$out"; then
  echo "PASS cross-module reject: an imported class's declared effect is enforced"
else
  failed "cross-module reject: expected a rule-8 mismatch naming demo.enc_quiet.Enc"
fi

# A wrap exported without `(..)` publishes its TYPE and not its constructor. This
# needs --package-root, so it has no home among the type_error fixtures; both halves
# live here. Construction and the destructor pattern resolve on different paths and
# report different diagnostics, so each is asserted separately — a gate on one alone
# would let an importer unwrap a value it cannot build.
check_out --package-root "$PKG_ROOT" "$FIX/app_wrap_ctor_construct.spr"
ctor="$out"
ctor_rc=$rc
if grep -qF 'Unknown variable: sealed.Sealed' <<<"$out"; then
  echo "PASS wrap opacity: an abstract wrap's constructor is unreachable cross-module"
else
  failed "wrap opacity: expected 'Unknown variable: sealed.Sealed'"
fi

check_out --package-root "$PKG_ROOT" "$FIX/app_wrap_ctor_pattern.spr"
pat="$out"
pat_rc=$rc
if grep -qF 'Unknown constructor: sealed.Sealed' <<<"$out"; then
  echo "PASS wrap opacity: the destructor pattern is hidden with the constructor"
else
  failed "wrap opacity: expected 'Unknown constructor: sealed.Sealed'"
fi

# Rejecting it is half the job; saying WHY is the other half. Bare "Unknown
# variable" reads as a typo and sends the reader hunting for a missing
# declaration instead of a missing marker, so both halves assert the note too.
for case_name in construct pattern; do
  case "$case_name" in
    construct) out="$ctor"; rc=$ctor_rc ;;
    pattern) out="$pat"; rc=$pat_rc ;;
  esac
  if grep -qF 'declares `Sealed`, which is not in scope here' <<<"$out" &&
     grep -qF 'exported only with `(..)`' <<<"$out"; then
    echo "PASS wrap opacity ($case_name): the diagnostic names the module and the marker"
  else
    failed "wrap opacity ($case_name): expected a note naming demo.sealed and \`(..)\`"
  fi
  # A canonical name is not a spelling the reader may write (spec-v0 §3), so a
  # diagnostic offering one would answer "how do I reach this?" with a rejected
  # form. Name the module and the symbol separately.
  if grep -qF 'demo.sealed.Sealed' <<<"$out"; then
    failed "wrap opacity ($case_name): diagnostic leaks the canonical name"
  fi
done

# A record exported without `(..)` publishes its type and not its representation.
# The two halves are settled by DIFFERENT components — construction by the bundler
# (the name is never bound), field access by inference (the name resolved; the
# type is what is private) — so one passing says nothing about the other.
check_out --package-root "$PKG_ROOT" "$FIX/app_record_construct.spr"
if grep -qF 'Unknown record type or field: recop.Opaque' <<<"$out" &&
   grep -qF 'exported only with `(..)`' <<<"$out"; then
  echo "PASS record opacity: an abstract record cannot be constructed cross-module"
else
  failed "record opacity: expected recop.Opaque construction rejected, with the \`(..)\` note"
fi

check_out --package-root "$PKG_ROOT" "$FIX/app_record_field.spr"
if grep -qF 'is abstract here' <<<"$out" &&
   grep -qF 'demo.recop' <<<"$out"; then
  echo "PASS record opacity: an abstract record's field is unreadable cross-module"
else
  failed "record opacity: expected a field read of demo.recop.Opaque to be refused"
fi

# A `wrap linear` is linear in its importer too, and an abstract one stays shut: its
# field is not linear, so unwrapping it would let an importer copy what it holds.
check_out --package-root "$PKG_ROOT" "$FIX/app_linear_wrap_leak.spr"
if grep -qF "linear value 't' is never used" <<<"$out"; then
  echo "PASS linear wrap: an importer sees the wrap as linear"
else
  failed "linear wrap: expected the unspent demo.linwrap.Token to be a leak"
fi

check_out --package-root "$PKG_ROOT" "$FIX/app_linear_wrap_pattern.spr"
if grep -qF 'Unknown constructor: linwrap.Token' <<<"$out"; then
  echo "PASS linear wrap: an abstract linear wrap cannot be unwrapped cross-module"
else
  failed "linear wrap: expected 'Unknown constructor: linwrap.Token'"
fi

# A published linear type is allowed when every field is linear, and refused in
# the module that declares it when one is not (docs/linear-publish-v0.md §4).
check_out --package-root "$PKG_ROOT" "$FIX/app_linear_publish_ok.spr"
if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
  echo "PASS linear publish: an importer destructures a published all-linear Pair"
else
  failed "linear publish: expected demo.linpub.Pair to check clean"
fi

check_out --package-root "$PKG_ROOT" "$FIX/app_linear_publish_bad.spr"
if grep -qF '`Ticket` is linear, and `(..)` publishes its constructor, but its field `Int` is not linear' <<<"$out"; then
  echo "PASS linear publish: a published linear wrap over Int is refused at its module"
else
  failed "linear publish: expected demo.linpub_bad.Ticket to be refused"
fi

# The batch CLI's half of the canonical-name rule. The env front end has its own
# (tests/stdlib/compiler/test_canonical_name_rejected.spr); the two have diverged
# before over package roots, which is why neither stands in for the other.
check_out --package-root "$PKG_ROOT" "$FIX/app_canonical_name.spr"
if grep -qF 'is a canonical name, not a way to reach a module' <<<"$out"; then
  echo "PASS canonical name: a private binding is unreachable by its bundled name"
else
  failed "canonical name: expected demo.dup_one.hidden_helper to be rejected"
fi

# The one-constructor clash (#422) reads the prelude's types as in scope everywhere.
# A `no_prelude` entry has the prelude's empty module name, so its own types must
# not count for a module it imports. Needs a second module, hence here.
check_out --package-root "$PKG_ROOT" "$FIX/app_no_prelude_ctor_clash.spr"
if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
  echo "PASS no_prelude: an entry's own type does not clash with an imported constructor"
else
  failed "no_prelude: demo.tokbar's constructor \`Token\` was judged against the entry's types"
fi

# `no_prelude` is a header line, so an import after it is still read.
check_out --package-root "$PKG_ROOT" "$FIX/app_no_prelude_then_import.spr"
if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
  echo "PASS no_prelude: an import after the directive resolves"
else
  failed "no_prelude: the import after \`no_prelude\` was dropped"
fi

# A clash inside an IMPORTED module is still reported, but without its position: a
# line:col from demo.clashdep would be read against the entry file.
check_out --package-root "$PKG_ROOT" "$FIX/app_dep_ctor_clash.spr"
if grep -q '^ERROR: bundle: type `demo.clashdep.Bad` has one constructor' <<<"$out"; then
  echo "PASS dependency clash: reported by module, with no position from the other file"
else
  failed "dependency clash: expected a position-less error naming demo.clashdep.Bad"
fi

# An extern whose signature WRITES `local` anywhere is private to its module, not only
# one with a top-level `local` parameter: `local` inside the return type or a
# parameter's type lends a borrowed value to a closure the C body may keep.
for shape in return param_type; do
  check_out --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_in_$shape.spr"
  if grep -qF 'is private to `demo.locext`' <<<"$out"; then
    echo "PASS private extern ($shape): a nested \`local\` makes the extern private to its module"
  else
    failed "private extern ($shape): expected the extern to be rejected as private to demo.locext"
  fi
done

# `local` reached through a type alias counts: in the extern's own module, and from
# another module through a chain of two aliases.
for shape in alias imported_alias; do
  check_out --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_via_$shape.spr"
  if grep -qF 'is private to `demo.alias' <<<"$out"; then
    echo "PASS private extern ($shape): \`local\` behind an alias makes the extern private"
  else
    failed "private extern ($shape): expected the extern to be rejected as private"
  fi
done

# ...and through any type definition the signature names: a sum's constructor
# field, a record field, a wrap. The recursive type has no `local`, so its extern
# stays public: that guards the cycle check and against over-marking.
for shape in adt record wrap; do
  check_out --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_in_$shape.spr"
  if grep -qF 'is private to `demo.adtext`' <<<"$out"; then
    echo "PASS private extern ($shape): \`local\` in a type definition makes the extern private"
  else
    failed "private extern ($shape): expected the extern to be rejected as private to demo.adtext"
  fi
done

check_out --package-root "$PKG_ROOT" "$FIX/app_extern_recursive_type_stays_public.spr"
if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
  echo "PASS private extern: a recursive type with no \`local\` leaves its extern public"
else
  failed "private extern: an extern naming a recursive type with no \`local\` was rejected"
fi

# Two declarations of one extern that differ only in variable names agree.
check_out --package-root "$PKG_ROOT" "$FIX/app_extern_redeclare_renamed_vars.spr"
if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
  echo "PASS extern redeclaration: renamed type and effect variables are the same signature"
else
  failed "extern redeclaration: renamed variables were judged a different signature"
fi

# An extern is private to its module unless that module writes `export extern fn`
# (docs/extern-visibility-v0.md). The `local` cases above are the special case: those
# stay private whether or not `export` is written, and `export` on one is an error.
check_out --package-root "$PKG_ROOT" "$FIX/app_private_extern_general_reached.spr"
if grep -qF '`__priv_add` is private to `demo.privext`. Mark it `export extern fn` there, or declare it in this module' <<<"$out"; then
  echo "PASS extern visibility: an unexported extern is private to its module"
else
  failed "extern visibility: expected __priv_add to be rejected as private to demo.privext"
fi

# A headerless `no_prelude` entry has module name "" but is not the prelude: its own
# extern declaration must not export the name to another module.
check_out --package-root "$PKG_ROOT" "$FIX/app_no_prelude_entry_extern_stays_private.spr"
if grep -qF '`__priv_add` is private to' <<<"$out"; then
  echo "PASS extern visibility: a no_prelude entry's extern stays private"
else
  failed "extern visibility: a no_prelude entry's extern was exported to another module"
fi

for shape in own_and_exported redeclared; do
  check_out --package-root "$PKG_ROOT" "$FIX/app_private_extern_$shape.spr"
  if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
    echo "PASS extern visibility ($shape): accepted"
  else
    failed "extern visibility ($shape): a legal use of an extern was rejected"
  fi
done

# Exportedness is per NAME across the build: one module's private redeclaration must
# not hide another module's exported extern from a third. Both import orders, so the
# private declaration is last in one bundle and first in the other.
for order in ab ba; do
  check_out --package-root "$PKG_ROOT" "$FIX/app_extern_collision_$order.spr"
  if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
    echo "PASS extern visibility (collision $order): a private redeclaration does not hide an exported extern"
  else
    failed "extern visibility (collision $order): the exported extern was hidden by a private redeclaration"
  fi
done

# The prelude's externs and the `no_prelude` floor's are exported implicitly.
for shape in prelude_extern_reachable no_prelude_floor_extern; do
  check_out --package-root "$PKG_ROOT" "$FIX/app_$shape.spr"
  if grep -q '^OK$' <<<"$out" && ! errors "$out" >/dev/null; then
    echo "PASS extern visibility ($shape): accepted"
  else
    failed "extern visibility ($shape): a prelude or floor extern was rejected"
  fi
done

check_out --package-root "$PKG_ROOT" "$FIX/app_export_extern_local.spr"
if grep -qF '`export` on extern `__bad_export_local` has no effect: its signature writes `local`, so it is reachable only from `demo.exportlocal`. Remove `export`' <<<"$out"; then
  echo "PASS extern visibility: \`export\` on an extern that writes \`local\` is an error"
else
  failed "extern visibility: expected \`export\` on a \`local\` extern to be rejected"
fi

# An importer's instance is not in scope in the module it imports, which is
# checked first. Two halves: a method call, and a `try` whose instance is fusable.
check_out --package-root "$PKG_ROOT" "$FIX/app_importer_instance_not_in_import.spr"
if grep -qF 'No instance of ToString for demo.orphan_show.Foo in function demo.orphan_show.describe' <<<"$out"; then
  echo "PASS instance scope: an importer's instance does not reach the module it imports"
else
  failed "instance scope: expected no ToString instance in demo.orphan_show.describe"
fi

check_out --package-root "$PKG_ROOT" "$FIX/app_importer_propagate_not_in_import.spr"
if grep -qF '`demo.orphan_try.Fetch Int` has no `Propagate` instance in function demo.orphan_try.bump' <<<"$out"; then
  echo "PASS instance scope: an importer's Propagate instance does not fuse a \`try\` it imports"
else
  failed "instance scope: expected no Propagate instance in demo.orphan_try.bump"
fi

# A method is reached as a value is (docs/qualified-class-methods-v0.md, rule 3):
# a module bundled but not imported gives no method, and a class import binds its
# methods, so one beside a function import of the same name is bound twice.
check_out --package-root "$PKG_ROOT" "$FIX/app_method_not_imported.spr"
if grep -qF 'Unknown variable: tally' <<<"$out"; then
  echo "PASS method scope: a method of a module not imported here is unknown"
else
  failed "method scope: expected 'Unknown variable: tally'"
fi

check_out --package-root "$PKG_ROOT" "$FIX/app_class_import_method_conflict.spr"
if grep -qF '`tally` is imported twice in this file' <<<"$out"; then
  echo "PASS method scope: a class import binds its methods"
else
  failed "method scope: expected \`tally\` imported twice"
fi

check_out --package-root "$PKG_ROOT" "$FIX/app_import_hidden_method.spr"
if grep -qF '`peek` is a method of class `Hidden`, which is not exported' <<<"$out"; then
  echo "PASS method scope: importing a hidden class's method names the class"
else
  failed "method scope: expected the diagnostic to name class \`Hidden\`"
fi

# A record's constructor is named after its type, so an abstract record and a sum
# constructor of the same name are one constructor declared twice. Rejected in the
# declaring module; without the rule the sum's exported `Token` bound to the same
# canonical name as the abstract record's constructor, and an importer could build
# the record directly.
check_out --package-root "$PKG_ROOT" "$FIX/app_record_forge_via_ctor.spr"
if grep -qF '`demo.recforge.Token` is defined more than once in this module' <<<"$out"; then
  echo "PASS record ctor clash: an abstract record sharing a sum constructor's name is rejected"
else
  failed "record ctor clash: expected \`demo.recforge.Token\` is defined more than once in this module"
fi

[ "$fail" -eq 0 ] && echo "==> package-resolution gate: OK" || echo "==> package-resolution gate: FAILED"
exit $fail
