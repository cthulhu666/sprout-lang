#!/usr/bin/env bash
# Reject a compiler string literal that names an unexported prelude `fn` or `let`.
#
# The bundler rejects such a name in source (spec-v0 §3.1), but a name the
# compiler writes itself after bundling never passes that check, so it would
# reach a private helper silently. The rule holds only while every generated
# name is exported; this keeps it so.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

names=$(grep -E '^(fn|let) [a-z_0-9]+' stdlib/prelude.sprout \
  | sed -E 's/^(fn|let) ([a-z_0-9]+).*/\2/' | sort -u || true)
if [[ -z "$names" ]]; then
  echo "prelude-private-names: OK — no unexported names."
  exit 0
fi

hits=$(sed 's/.*/"&"/' <<<"$names" | grep -nFf - stdlib/compiler/*.sprout || true)

if [[ -n "$hits" ]]; then
  echo "prelude-private-names: FAIL — the compiler names an unexported prelude fn/let:"
  printf '%s\n' "$hits" | sed 's/^/  /'
  echo "Export it in stdlib/prelude.sprout, or generate an exported name."
  exit 1
fi
echo "prelude-private-names: OK — $(wc -l <<<"$names" | tr -d ' ') unexported names, none named by the compiler."
