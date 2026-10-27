#!/usr/bin/env bash
# What LLVM's loop vectorizer and Polly say about bench/llvm_remarks/kernels.sprout.
#
# Emits the kernels' IR with the stage-1 compiler, compiles it at -O3 with Polly on, and
# prints one line per (function, pass, remark) with a count. --no-ovf first deletes every
# overflow-check branch, to see the next blocker. docs/llvm-value-types-v0.md.
#
# usage: bench/llvm_remarks/remarks.sh [--no-ovf]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
STAGE1="$REPO/build/compile_driver_bin_stage1"

if [[ ! -x "$STAGE1" ]]; then
  echo "ERROR: $STAGE1 not found (run: just bootstrap-from-seed)" >&2
  exit 1
fi

TMPD="$(mktemp -d "${TMPDIR:-/tmp}/sprout_remarks_XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

(cd "$REPO" && "$STAGE1" --emit-ir stdlib "$DIR/kernels.sprout") > "$TMPD/k.ll"

if [[ "${1:-}" == "--no-ovf" ]]; then
  sed -E 's/br i1 %t\$[0-9]+\$ovf, label %ovfpanic_[0-9]+, label (%ovfok_[0-9]+)/br label \1/' \
    "$TMPD/k.ll" > "$TMPD/k2.ll"
  mv "$TMPD/k2.ll" "$TMPD/k.ll"
fi

(cd "$REPO" && mise exec -- clang -O3 -mllvm -polly -mllvm -polly-process-unprofitable \
  -fsave-optimization-record -foptimization-record-file="$TMPD/k.yaml" \
  -Wno-override-module -c "$TMPD/k.ll" -o "$TMPD/k.o")

# One YAML document per remark; keep the kernels' vectorizer and Polly verdicts.
awk '
  /^--- / { flush(); pass = fn = name = ""; next }
  /^Pass:/ { pass = $2 }
  /^Name:/ { name = $2 }
  /^Function:/ { fn = $2; gsub(/\047/, "", fn) }
  function flush() {
    if (fn ~ /(axpy_go|mm_[ijk]|sum_go)/ && pass ~ /^(loop-vectorize|polly-detect)$/)
      count[fn " " pass " " name]++
  }
  END { flush(); for (k in count) print count[k], k }
' "$TMPD/k.yaml" | sort -k2
