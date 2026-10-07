#!/usr/bin/env bash
# Every top-level stdlib module has a `## stdlib.<name>` section in docs/stdlib-reference.md.
#
# When the stdlib half was split out of builtins-reference.md, 20 of 35 modules had a reference and
# nothing had noticed the rest. UNDOCUMENTED lists them, and it only shrinks: a listed module must
# NOT have a section, so documenting one forces its removal, and an unlisted module must have one.
# Nested modules (stdlib/fs/path.sprout, stdlib/math/int.sprout) are out of scope, as they are for
# the REPL's module-completion list.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

DOC=docs/stdlib-reference.md
UNDOCUMENTED=" args bits chan http_middleware linalg log mutable process repl rng stamped task template test version "

fail=0
for file in stdlib/*.sprout; do
  name=$(basename "$file" .sprout)
  [ "$name" = prelude ] && continue
  has=0; grep -qE "^## stdlib\.${name}( |$)" "$DOC" && has=1
  listed=0; case "$UNDOCUMENTED" in *" $name "*) listed=1 ;; esac
  if [ "$has" = 0 ] && [ "$listed" = 0 ]; then
    echo "stdlib-reference: stdlib.$name has no '## stdlib.$name' section in $DOC" >&2
    fail=1
  elif [ "$has" = 1 ] && [ "$listed" = 1 ]; then
    echo "stdlib-reference: stdlib.$name is documented now; remove it from UNDOCUMENTED in $0" >&2
    fail=1
  fi
done

for name in $UNDOCUMENTED; do
  if [ ! -f "stdlib/$name.sprout" ]; then
    echo "stdlib-reference: UNDOCUMENTED lists stdlib.$name, which no longer exists" >&2
    fail=1
  fi
done

if [ "$fail" = 0 ]; then echo "stdlib-reference: ok"; fi
exit "$fail"
