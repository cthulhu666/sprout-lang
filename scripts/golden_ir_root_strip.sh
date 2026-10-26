#!/usr/bin/env bash
# scripts/golden_ir_root_strip.sh <old.ll> <new.ll>
#
# Diff two emitted modules with GC rooting stripped from both. Frame-slot rooting
# (docs/frame-slot-rooting-v0.md) rewrites the roots of nearly every function, which a
# golden-IR diff cannot be read through. The rest of the IR must not move: this prints the
# residual diff (unified) and exits 1 if there is one, 0 if the two are the same modulo rooting.
#
# Stripped from both sides, per function:
#   - calls to @sprout_gc_push_i64_root, @sprout_gc_pop_roots, @sprout_gc_push_scan_root;
#   - every `alloca i64` (root slots, scalar and heap TCO slots) and the frame's
#     `alloca [N x i64]`, the getelementptrs of the frame, and every store into one of those;
#   - @llvm.memset calls, @llvm.stacksave and @llvm.stackrestore calls;
#   - the `declare` lines of all of the above;
#   - blank lines inside a function (an IRTcoEntry with no slots left lowers to one).
# Then every %t$N is renumbered in order of first appearance, per function: rooting temps share
# the translator's counter, so added and removed ops would otherwise shift the survivors.
#
# What stays: loads of a TCO slot and every other instruction, so a changed argument, block,
# branch or call is still a residual. @sprout_gc_register_i64_root (permanent global roots) is
# not touched.
set -uo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <old.ll> <new.ll>" >&2
  exit 2
fi

strip_ir() {
  awk '
    function flush(   i, line, name, base, k, out) {
      delete roots; delete frames; delete allocas; delete slots; delete ren
      nren = 0
      for (i = 1; i <= n; i++) {
        line = buf[i]
        if (match(line, /call i64 @sprout_gc_push_i64_root\(ptr %[^)]*\)/)) {
          name = substr(line, RSTART, RLENGTH); sub(/.*\(ptr /, "", name); sub(/\)$/, "", name)
          roots[name] = 1
        } else if (match(line, /call i64 @sprout_gc_push_scan_root\(ptr %[^,]*,/)) {
          name = substr(line, RSTART, RLENGTH); sub(/.*\(ptr /, "", name); sub(/,$/, "", name)
          frames[name] = 1
        } else if (line ~ /^ *%[^ ]+ = alloca i64$/) {
          name = line; sub(/^ */, "", name); sub(/ = alloca i64$/, "", name)
          allocas[name] = 1
        }
      }
      for (i = 1; i <= n; i++) {
        line = buf[i]
        if (line ~ /^ *%[^ ]+ = getelementptr i64, ptr %[^ ,]+, i64 [0-9]+$/) {
          base = line; sub(/^.*getelementptr i64, ptr /, "", base); sub(/,.*$/, "", base)
          if (base in frames) {
            name = line; sub(/^ */, "", name); sub(/ = .*$/, "", name)
            slots[name] = 1
          }
        }
      }
      for (i = 1; i <= n; i++) {
        line = buf[i]
        if (line ~ /^[ \t]*$/) continue
        if (line ~ /call i64 @sprout_gc_push_i64_root\(/) continue
        if (line ~ /call i64 @sprout_gc_pop_roots\(/) continue
        if (line ~ /call i64 @sprout_gc_push_scan_root\(/) continue
        if (line ~ /call void @llvm\.memset\.p0\.i64\(/) continue
        if (line ~ /call ptr @llvm\.stacksave\(\)/) continue
        if (line ~ /call void @llvm\.stackrestore\(/) continue
        if (line ~ /^ *%[^ ]+ = alloca i64$/) continue
        if (line ~ /^ *%[^ ]+ = alloca \[[0-9]+ x i64\]$/) continue
        if (line ~ /^ *%[^ ]+ = getelementptr i64, ptr %[^ ,]+, i64 [0-9]+$/) {
          base = line; sub(/^.*getelementptr i64, ptr /, "", base); sub(/,.*$/, "", base)
          if (base in frames) continue
        }
        if (line ~ /^ *store i64 [^,]+, ptr %[^ ]+$/) {
          base = line; sub(/^.*, ptr /, "", base)
          if ((base in allocas) || (base in slots) || (base in frames)) continue
        }
        print renumber(line)
      }
    }
    # Rename %t$<digits> by first appearance in this function.
    function renumber(line,   out, rest, tok) {
      out = ""; rest = line
      while (match(rest, /%t\$[0-9]+/)) {
        tok = substr(rest, RSTART, RLENGTH)
        if (!(tok in ren)) { ren[tok] = "%t$" nren; nren++ }
        out = out substr(rest, 1, RSTART - 1) ren[tok]
        rest = substr(rest, RSTART + RLENGTH)
      }
      return out rest
    }
    /^declare / {
      if ($0 ~ /@(sprout_gc_push_i64_root|sprout_gc_pop_roots|sprout_gc_push_scan_root|llvm\.memset\.p0\.i64|llvm\.stacksave|llvm\.stackrestore)\(/) next
      print; next
    }
    /^define / { infn = 1; n = 0 }
    infn { buf[++n] = $0; if ($0 == "}") { flush(); infn = 0 }; next }
    { print }
  ' "$1"
}

old_stripped=$(mktemp /tmp/golden_strip_old_XXXXXX)
new_stripped=$(mktemp /tmp/golden_strip_new_XXXXXX)
trap 'rm -f "$old_stripped" "$new_stripped"' EXIT
strip_ir "$1" > "$old_stripped"
strip_ir "$2" > "$new_stripped"
diff -u --label "$1 (stripped)" --label "$2 (stripped)" "$old_stripped" "$new_stripped"
