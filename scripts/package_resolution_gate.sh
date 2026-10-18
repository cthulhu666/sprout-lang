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
DRV="$ROOT/build/compile_driver_bin_stage1"
STDLIB="$ROOT/stdlib"
FIX="$ROOT/tests/conformance/package_resolution"
PKG_ROOT="$FIX/roots"
APP="$FIX/app.spr"
fail=0

# Real diagnostics are `<pos>ERROR: ...` or `Unknown variable ...`; the check phase
# also dumps the type env (names like `result_map_error`), so match error markers
# precisely (case-sensitive `ERROR:` / `Unknown variable`), not a bare `error`.
errors() { echo "$1" | grep -E 'ERROR:|Unknown variable'; }

# Positive: registered extra root -> import resolves, check reports OK, no errors.
pos="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$APP" 2>&1)"
if echo "$pos" | grep -q '^OK$' && ! errors "$pos" >/dev/null; then
  echo "PASS positive: second-root import resolved and type-checked"
else
  echo "FAIL positive: --package-root did not resolve demo.greet"
  errors "$pos" | head -5
  fail=1
fi

# Negative: no extra root -> the import of demo.greet is itself the error.
neg="$("$DRV" --phase check "$STDLIB" "$APP" 2>&1)"
if echo "$neg" | grep -q 'import demo.greet: unknown module'; then
  echo "PASS negative: unregistered dotted import correctly failed to resolve"
else
  echo "FAIL negative: expected an unknown-module error on the demo.greet import without --package-root"
  fail=1
fi

# Header check: an imported file must declare a `module` header
# (docs/prelude-scope-v0.md §4.2 step 3). The root IS registered here, so
# resolution succeeds and the header check is the only thing that can reject it.
# Asserted on the MESSAGE, not just the exit status: the whole point of the
# diagnostic is that it names the file and the cause, where the old behaviour
# reported `... is defined more than once in this module` about whichever symbol
# happened to collide with the prelude — or nothing at all.
hdr="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_headerless.spr" 2>&1)"
if echo "$hdr" | grep -q 'no `module` header' && echo "$hdr" | grep -q 'headerless.sprout'; then
  echo "PASS header: headerless imported file diagnosed by path and cause"
else
  echo "FAIL header: expected a 'no \`module\` header' diagnostic naming headerless.sprout"
  echo "$hdr" | head -5
  fail=1
fi

# Class-name collision across roots: two modules declaring a class with the same
# SHORT name and different declared effects. Rule 8's instance/class effect check
# keys on the class, and a short-name-only key collapses the two — rejecting a
# legal program while naming a class that declares the opposite effect.
# Here because the shape needs two sibling modules, which needs a package root.
col="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_class_name_collision.spr" 2>&1)"
if echo "$col" | grep -q '^OK$' && ! errors "$col" >/dev/null; then
  echo "PASS collision: same-short-named classes from two modules judged separately"
else
  echo "FAIL collision: legal program rejected — class-keyed effect lookup collapsed two classes"
  errors "$col" | head -3
  fail=1
fi

# The firing half of the same lookup. The assertion above is acceptance-only, so a
# key that never matches an imported class passes it; this one fails unless the
# effect declared by a class in ANOTHER module is found and enforced.
rej="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_cross_module_class_reject.spr" 2>&1)"
if echo "$rej" | grep -qF 'of class `demo.enc_quiet.Enc`: declared !{IO}, but the class declares pure'; then
  echo "PASS cross-module reject: an imported class's declared effect is enforced"
else
  echo "FAIL cross-module reject: expected a rule-8 mismatch naming demo.enc_quiet.Enc"
  echo "$rej" | tail -3
  fail=1
fi

# A wrap exported without `(..)` publishes its TYPE and not its constructor. This
# needs --package-root, so it has no home among the type_error fixtures; both halves
# live here. Construction and the destructor pattern resolve on different paths and
# report different diagnostics, so each is asserted separately — a gate on one alone
# would let an importer unwrap a value it cannot build.
ctor="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_wrap_ctor_construct.spr" 2>&1)"
if echo "$ctor" | grep -qF 'Unknown variable: sealed.Sealed'; then
  echo "PASS wrap opacity: an abstract wrap's constructor is unreachable cross-module"
else
  echo "FAIL wrap opacity: expected 'Unknown variable: sealed.Sealed'"
  echo "$ctor" | tail -3
  fail=1
fi

pat="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_wrap_ctor_pattern.spr" 2>&1)"
if echo "$pat" | grep -qF 'Unknown constructor: sealed.Sealed'; then
  echo "PASS wrap opacity: the destructor pattern is hidden with the constructor"
else
  echo "FAIL wrap opacity: expected 'Unknown constructor: sealed.Sealed'"
  echo "$pat" | tail -3
  fail=1
fi

# Rejecting it is half the job; saying WHY is the other half. Bare "Unknown
# variable" reads as a typo and sends the reader hunting for a missing
# declaration instead of a missing marker, so both halves assert the note too.
for half in "construct:$ctor" "pattern:$pat"; do
  case_name="${half%%:*}"
  out="${half#*:}"
  if echo "$out" | grep -qF 'declares `Sealed`, which is not in scope here' &&
     echo "$out" | grep -qF 'exported only with `(..)`'; then
    echo "PASS wrap opacity ($case_name): the diagnostic names the module and the marker"
  else
    echo "FAIL wrap opacity ($case_name): expected a note naming demo.sealed and \`(..)\`"
    echo "$out" | tail -3
    fail=1
  fi
  # A canonical name is not a spelling the reader may write (spec-v0 §3), so a
  # diagnostic offering one would answer "how do I reach this?" with a rejected
  # form. Name the module and the symbol separately.
  if echo "$out" | grep -qF 'demo.sealed.Sealed'; then
    echo "FAIL wrap opacity ($case_name): diagnostic leaks the canonical name"
    echo "$out" | tail -3
    fail=1
  fi
done

# A record exported without `(..)` publishes its type and not its representation.
# The two halves are settled by DIFFERENT components — construction by the bundler
# (the name is never bound), field access by inference (the name resolved; the
# type is what is private) — so one passing says nothing about the other.
rcon="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_record_construct.spr" 2>&1)"
if echo "$rcon" | grep -qF 'Unknown record type or field: recop.Opaque' &&
   echo "$rcon" | grep -qF 'exported only with `(..)`'; then
  echo "PASS record opacity: an abstract record cannot be constructed cross-module"
else
  echo "FAIL record opacity: expected recop.Opaque construction rejected, with the \`(..)\` note"
  echo "$rcon" | tail -3
  fail=1
fi

rfld="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_record_field.spr" 2>&1)"
if echo "$rfld" | grep -qF 'is abstract here' &&
   echo "$rfld" | grep -qF 'demo.recop'; then
  echo "PASS record opacity: an abstract record's field is unreadable cross-module"
else
  echo "FAIL record opacity: expected a field read of demo.recop.Opaque to be refused"
  echo "$rfld" | tail -3
  fail=1
fi

# The batch CLI's half of the canonical-name rule. The env front end has its own
# (tests/stdlib/compiler/test_canonical_name_rejected.spr); the two have diverged
# before over package roots, which is why neither stands in for the other.
can="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_canonical_name.spr" 2>&1)"
if echo "$can" | grep -qF 'is a canonical name, not a way to reach a module'; then
  echo "PASS canonical name: a private binding is unreachable by its bundled name"
else
  echo "FAIL canonical name: expected demo.dup_one.hidden_helper to be rejected"
  echo "$can" | tail -3
  fail=1
fi

# The one-constructor clash (#422) reads the prelude's types as in scope everywhere.
# A `no_prelude` entry has the prelude's empty module name, so its own types must
# not count for a module it imports. Needs a second module, hence here.
npc="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_no_prelude_ctor_clash.spr" 2>&1)"
if echo "$npc" | grep -q '^OK$' && ! errors "$npc" >/dev/null; then
  echo "PASS no_prelude: an entry's own type does not clash with an imported constructor"
else
  echo "FAIL no_prelude: demo.tokbar's constructor \`Token\` was judged against the entry's types"
  errors "$npc" | head -3
  fail=1
fi

# `no_prelude` is a header line, so an import after it is still read.
npi="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_no_prelude_then_import.spr" 2>&1)"
if echo "$npi" | grep -q '^OK$' && ! errors "$npi" >/dev/null; then
  echo "PASS no_prelude: an import after the directive resolves"
else
  echo "FAIL no_prelude: the import after \`no_prelude\` was dropped"
  errors "$npi" | head -3
  fail=1
fi

# A clash inside an IMPORTED module is still reported, but without its position: a
# line:col from demo.clashdep would be read against the entry file.
dep="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_dep_ctor_clash.spr" 2>&1)"
if echo "$dep" | grep -q '^ERROR: bundle: type `demo.clashdep.Bad` has one constructor'; then
  echo "PASS dependency clash: reported by module, with no position from the other file"
else
  echo "FAIL dependency clash: expected a position-less error naming demo.clashdep.Bad"
  errors "$dep" | head -3
  fail=1
fi

# An extern whose signature WRITES `local` anywhere is private to its module, not only
# one with a top-level `local` parameter: `local` inside the return type or a
# parameter's type lends a borrowed value to a closure the C body may keep.
for shape in return param_type; do
  pex="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_in_$shape.spr" 2>&1)"
  if echo "$pex" | grep -qF 'is private to `demo.locext`'; then
    echo "PASS private extern ($shape): a nested \`local\` makes the extern private to its module"
  else
    echo "FAIL private extern ($shape): expected the extern to be rejected as private to demo.locext"
    echo "$pex" | tail -3
    fail=1
  fi
done

# `local` reached through a type alias counts: in the extern's own module, and from
# another module through a chain of two aliases.
for shape in alias imported_alias; do
  pal="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_via_$shape.spr" 2>&1)"
  if echo "$pal" | grep -qF 'is private to `demo.alias'; then
    echo "PASS private extern ($shape): \`local\` behind an alias makes the extern private"
  else
    echo "FAIL private extern ($shape): expected the extern to be rejected as private"
    echo "$pal" | tail -3
    fail=1
  fi
done

# ...and through any type definition the signature names: a sum's constructor
# field, a record field, a wrap. The recursive type has no `local`, so its extern
# stays public: that guards the cycle check and against over-marking.
for shape in adt record wrap; do
  pty="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_private_extern_local_in_$shape.spr" 2>&1)"
  if echo "$pty" | grep -qF 'is private to `demo.adtext`'; then
    echo "PASS private extern ($shape): \`local\` in a type definition makes the extern private"
  else
    echo "FAIL private extern ($shape): expected the extern to be rejected as private to demo.adtext"
    echo "$pty" | tail -3
    fail=1
  fi
done

rec="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_extern_recursive_type_stays_public.spr" 2>&1)"
if echo "$rec" | grep -q '^OK$' && ! errors "$rec" >/dev/null; then
  echo "PASS private extern: a recursive type with no \`local\` leaves its extern public"
else
  echo "FAIL private extern: an extern naming a recursive type with no \`local\` was rejected"
  errors "$rec" | head -3
  fail=1
fi

# Two declarations of one extern that differ only in variable names agree.
rn="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_extern_redeclare_renamed_vars.spr" 2>&1)"
if echo "$rn" | grep -q '^OK$' && ! errors "$rn" >/dev/null; then
  echo "PASS extern redeclaration: renamed type and effect variables are the same signature"
else
  echo "FAIL extern redeclaration: renamed variables were judged a different signature"
  errors "$rn" | head -3
  fail=1
fi

# An importer's instance is not in scope in the module it imports, which is
# checked first. Two halves: a method call, and a `try` whose instance is fusable.
inst="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_importer_instance_not_in_import.spr" 2>&1)"
if echo "$inst" | grep -qF 'No instance of ToString for demo.orphan_show.Foo in function demo.orphan_show.describe'; then
  echo "PASS instance scope: an importer's instance does not reach the module it imports"
else
  echo "FAIL instance scope: expected no ToString instance in demo.orphan_show.describe"
  echo "$inst" | tail -3
  fail=1
fi

prop="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_importer_propagate_not_in_import.spr" 2>&1)"
if echo "$prop" | grep -qF '`demo.orphan_try.Fetch Int` has no `Propagate` instance in function demo.orphan_try.bump'; then
  echo "PASS instance scope: an importer's Propagate instance does not fuse a \`try\` it imports"
else
  echo "FAIL instance scope: expected no Propagate instance in demo.orphan_try.bump"
  echo "$prop" | tail -3
  fail=1
fi

# A method is reached as a value is (docs/qualified-class-methods-v0.md, rule 3):
# a module bundled but not imported gives no method, and a class import binds its
# methods, so one beside a function import of the same name is bound twice.
mni="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_method_not_imported.spr" 2>&1)"
if echo "$mni" | grep -qF 'Unknown variable: tally'; then
  echo "PASS method scope: a method of a module not imported here is unknown"
else
  echo "FAIL method scope: expected 'Unknown variable: tally'"
  echo "$mni" | tail -3
  fail=1
fi

mcf="$("$DRV" --phase check "$STDLIB" --package-root "$PKG_ROOT" "$FIX/app_class_import_method_conflict.spr" 2>&1)"
if echo "$mcf" | grep -qF '`tally` is imported twice in this file'; then
  echo "PASS method scope: a class import binds its methods"
else
  echo "FAIL method scope: expected \`tally\` imported twice"
  echo "$mcf" | tail -3
  fail=1
fi

[ "$fail" -eq 0 ] && echo "==> package-resolution gate: OK" || echo "==> package-resolution gate: FAILED"
exit $fail
