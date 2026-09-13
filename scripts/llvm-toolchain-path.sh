#!/usr/bin/env bash
set -euo pipefail

is_llvm_bindir() {
  local bindir="$1"
  local opt_version clang_version opt_major clang_major
  [[ -x "$bindir/opt" && -x "$bindir/clang" ]] || return 1
  opt_version="$($bindir/opt --version 2>/dev/null | sed -n '1,3p')"
  clang_version="$($bindir/clang --version 2>/dev/null | sed -n '1,3p')"
  [[ "$opt_version" == *LLVM* && "$clang_version" == *clang* ]] || return 1
  opt_major="$(sed -nE 's/.*version ([0-9]+).*/\1/p' <<<"$opt_version" | head -n1)"
  clang_major="$(sed -nE 's/.*version ([0-9]+).*/\1/p' <<<"$clang_version" | head -n1)"
  [[ -n "$opt_major" && -n "$clang_major" && "$opt_major" -ge 16 && "$clang_major" -ge 16 ]]
}

emit_if_valid() {
  local bindir="$1"
  if is_llvm_bindir "$bindir"; then
    (cd "$bindir" && pwd -P)
    exit 0
  fi
}

if [[ -n "${SPROUT_LLVM_BINDIR:-}" ]]; then
  emit_if_valid "$SPROUT_LLVM_BINDIR"
  exit 0
fi

if command -v opt >/dev/null 2>&1 && command -v clang >/dev/null 2>&1; then
  opt_bindir="$(dirname "$(command -v opt)")"
  clang_bindir="$(dirname "$(command -v clang)")"
  [[ "$opt_bindir" == "$clang_bindir" ]] && emit_if_valid "$opt_bindir"
fi

if command -v brew >/dev/null 2>&1; then
  brew_llvm="$(brew --prefix llvm 2>/dev/null || true)/bin"
  emit_if_valid "$brew_llvm"
fi

if [[ "$(uname -s)" == Linux ]]; then
  while IFS= read -r bindir; do
    emit_if_valid "$bindir"
  done < <(find /usr/lib -maxdepth 2 -type d -path '/usr/lib/llvm-*/bin' 2>/dev/null | sort -Vr)
fi

exit 0
