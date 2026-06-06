#!/usr/bin/env bash
# LLVM line-to-source-function diagnostic.
# Given a .ll file and a line number from an opt/clang error, prints the
# enclosing Sprout function name.
#
# Usage: scripts/llvm_diag.sh <ll_file> <line>
#
# Output examples:
#   line 130912 in: @stdlib.compiler.codegen.emit_fn
#   line 263090 in: @__sprout_lambda_1680_vec_sum_by  (header: ; __sprout_lambda_1680_vec_sum_by (in vec_sum_by))
#   line 42 in: <module-level section — before first define>
#
# Typical use: opt --passes=verify build/stage2.ll reports "error at line N"
#   → scripts/llvm_diag.sh build/stage2.ll N
#   → just llvm-where build/stage2.ll N
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: scripts/llvm_diag.sh <ll_file> <line>" >&2
  exit 2
fi

LL="$1"
TARGET="$2"

if [[ ! -f "$LL" ]]; then
  echo "Error: file not found: $LL" >&2
  exit 2
fi

if ! [[ "$TARGET" =~ ^[0-9]+$ ]]; then
  echo "Error: line must be a non-negative integer, got: $TARGET" >&2
  exit 2
fi

awk -v target="$TARGET" '
  /^; __sprout_lambda_/ { last_comment = $0 }
  /^define / {
    # Extract just the @symbol from: define <ret> @name(...)
    match($0, /@[A-Za-z0-9_.]+/)
    last_define = substr($0, RSTART, RLENGTH)
    last_define_line = NR
    last_comment_for_define = last_comment
    last_comment = ""
  }
  NR == target {
    if (last_define == "") {
      print "line " target " in: <module-level section — before first define>"
    } else if (last_comment_for_define != "") {
      print "line " target " in: " last_define "  (header: " last_comment_for_define ")"
    } else {
      print "line " target " in: " last_define
    }
    exit
  }
  END {
    if (NR < target) {
      print "Error: file has " NR " lines, target " target " is out of range" > "/dev/stderr"
      exit 2
    }
  }
' "$LL"
