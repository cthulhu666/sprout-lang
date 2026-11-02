#!/usr/bin/env bash
# Specialisation corpus check (docs/specialisation-v0.md §7 M2).
#
# Compiles every golden-IR corpus file (examples/*.sprout, tests/smoke_shapes/*.spr) with the
# pass off (SPROUT_OPT_OFF=spec) and with the default (pass on),
# using the invocation the golden scripts use. Checks:
#   A (--off-golden-ref <ref> only): the pass-off output equals the golden at <ref>, byte for byte.
#   B: every define the pass adds is a `$spec_` clone of a pass-off define, or a lambda/eta
#      wrapper; every define it removes has no reference left in the pass-on IR.
#   C: each program in tests/spec_corpus_run.txt prints the same text and exit status on and off.
# Each compile is bounded at 60 s, each run at 30 s.
#
# Run: mise exec -- just spec-corpus-check [--off-golden-ref <git-ref>]
set -uo pipefail

C="${SPROUT_STAGE1:-build/compile_driver_bin_stage1}"
GOLD="tests/golden/ir"
RUNLIST="tests/spec_corpus_run.txt"
TMP="$(mktemp -d /tmp/sprout_speccorpus_XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
COMPILE_LIMIT=60
RUN_LIMIT=30
fail=0
ref=""
case "${1:-}" in
  "") ;;
  --off-golden-ref) ref="${2:?--off-golden-ref needs a git ref}"; [ $# -eq 2 ] || { echo "usage: $0 [--off-golden-ref <ref>]" >&2; exit 2; } ;;
  *) echo "usage: $0 [--off-golden-ref <ref>]" >&2; exit 2 ;;
esac

if [[ "$(uname)" == "Darwin" ]]; then
  CLANG_EXTRA=(-framework Security -framework CoreFoundation)
else
  CLANG_EXTRA=()
fi

source "$(dirname "${BASH_SOURCE[0]}")/golden_ir_lib.sh"

gold_exists() { # <src>
  if [ -n "$ref" ]; then git cat-file -e "$ref:$GOLD/$(flatten_name "$1")" 2>/dev/null
  else [ -f "$GOLD/$(flatten_name "$1")" ]; fi
}

gold_show() { # <src>
  if [ -n "$ref" ]; then git show "$ref:$GOLD/$(flatten_name "$1")"; else cat "$GOLD/$(flatten_name "$1")"; fi
}

# <on|off> -> sets ENVS to the env argv for the compiler: `env` plus SPROUT_OPT_OFF=spec when off.
# Starts with `env` so the array is never empty (macOS bash 3.2 errors on "${empty[@]}" under -u).
set_envs() {
  ENVS=(env)
  [ "$1" = off ] && ENVS+=(SPROUT_OPT_OFF=spec)
  return 0
}

# <src> <on|off> <out.ll> -> compiles to <out.ll> (stdout) and <out.ll>.err; 0 ok, 1 fail, 2 timeout
compile() {
  local src="$1" mode="$2" ll="$3" ec
  set_envs "$mode"
  limited "$COMPILE_LIMIT" "${ENVS[@]}" "$C" --use-ir-codegen stdlib "$src" > "$ll" 2> "$ll.err"
  ec=$?
  [ "$ec" -eq 142 ] && return 2
  [ "$ec" -ne 0 ] && return 1
  opt --passes=verify "$ll" -o /dev/null 2> "$ll.verify" || return 1
  return 0
}

defs() { # <ll> -> sorted defined function names
  grep '^define ' "$1" | sed -n 's/^[^@]*@\([^(]*\)(.*/\1/p' | sed 's/^"\(.*\)"$/\1/' | LC_ALL=C sort -u
}

refs() { # <ll> -> sorted unique global names referenced (@name and @"quoted name", without @ or quotes)
  { grep -o '@[-A-Za-z$._0-9]*' "$1" | sed 's/^@//'
    grep -o '@"[^"]*"' "$1" | sed 's/^@"//; s/"$//'
  } | LC_ALL=C sort -u
}

# <name> -> 0 when the added name is allowed
added_ok() {
  local n="$1"
  case "$n" in
    __sprout_ir_lambda_*|__sprout_ir_eta_*) return 0 ;;
    *'$spec_'*) grep -qxF -- "${n%%\$spec_*}" "$TMP/off.defs" && return 0 ;;
  esac
  return 1
}

run_one() { # <binary> -> combined output plus an exit line
  local out ec
  out="$(limited "$RUN_LIMIT" "$@" 2>&1 </dev/null)"; ec=$?
  printf '%s\nexit=%s\n' "$out" "$ec"
}

nfiles=0
nruns=0
ncopies=0  # files whose pass-on IR has a copy; 0 means the pass never ran
for src in examples/*.sprout tests/smoke_shapes/*.spr; do
  [ -f "$src" ] || continue
  gold_exists "$src" || continue
  nfiles=$((nfiles + 1))
  name="$(flatten_name "$src")"
  ok=1
  for mode in off on; do
    compile "$src" "$mode" "$TMP/$name.$mode.ll"; rc=$?
    if [ "$rc" -eq 2 ]; then
      echo "FAIL: $src: $mode compile timed out after ${COMPILE_LIMIT}s"; fail=1; ok=0
    elif [ "$rc" -ne 0 ]; then
      echo "FAIL: $src: $mode compile or verify failed"
      head -5 "$TMP/$name.$mode.ll.err" "$TMP/$name.$mode.ll.verify" 2>/dev/null; fail=1; ok=0
    fi
  done
  [ "$ok" -eq 1 ] || continue

  if [ -n "$ref" ]; then
    # The golden scripts capture stdout and stderr together.
    if [ -s "$TMP/$name.off.ll.err" ]; then
      set_envs off
      limited "$COMPILE_LIMIT" "${ENVS[@]}" "$C" --use-ir-codegen stdlib "$src" > "$TMP/$name.off.2" 2>&1
    else
      cp "$TMP/$name.off.ll" "$TMP/$name.off.2"
    fi
    gold_show "$src" > "$TMP/$name.gold"
    if cmp -s "$TMP/$name.off.2" "$TMP/$name.gold"; then echo "ok: $src: off equals golden at $ref"
    else
      echo "FAIL: $src: off output differs from golden at $ref"
      diff "$TMP/$name.gold" "$TMP/$name.off.2" | head -10; fail=1
    fi
  fi

  defs "$TMP/$name.off.ll" > "$TMP/off.defs"
  defs "$TMP/$name.on.ll" > "$TMP/on.defs"
  grep -qF '$spec_' "$TMP/on.defs" && ncopies=$((ncopies + 1))
  bad=0
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    if ! added_ok "$n"; then echo "FAIL: $src: unexpected added define: $n"; bad=1; fi
  done < <(comm -13 "$TMP/off.defs" "$TMP/on.defs")
  refs "$TMP/$name.on.ll" > "$TMP/on.refs"
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    echo "FAIL: $src: removed define $n still referenced"; bad=1
  done < <(LC_ALL=C comm -23 "$TMP/off.defs" "$TMP/on.defs" | LC_ALL=C comm -12 - "$TMP/on.refs")
  if [ "$bad" -eq 0 ]; then echo "ok: $src: defines"; else fail=1; fi
done

if [ ! -f "$RUNLIST" ]; then echo "FAIL: missing $RUNLIST"; fail=1; fi
while IFS= read -r p || [ -n "$p" ]; do
  case "$p" in ''|'#'*) continue ;; esac
  if [ ! -f "$p" ]; then echo "FAIL: run list: $p does not exist"; fail=1; continue; fi
  if ! gold_exists "$p"; then echo "FAIL: run list: $p has no golden"; fail=1; continue; fi
  name="$(flatten_name "$p")"
  if [ ! -f "$TMP/$name.on.ll" ] || [ ! -f "$TMP/$name.off.ll" ]; then
    echo "FAIL: run list: $p was not compiled"; fail=1; continue
  fi
  nruns=$((nruns + 1))
  built=1
  for mode in off on; do
    clang "$TMP/$name.$mode.ll" runtime/*.c -O0 "${CLANG_EXTRA[@]}" -o "$TMP/$name.$mode" 2> "$TMP/$name.$mode.link" || {
      echo "FAIL: $p: $mode link failed"; head -5 "$TMP/$name.$mode.link"; fail=1; built=0; }
  done
  [ "$built" -eq 1 ] || continue
  run_one "$TMP/$name.off" > "$TMP/$name.off.out"
  run_one "$TMP/$name.on" > "$TMP/$name.on.out"
  if cmp -s "$TMP/$name.off.out" "$TMP/$name.on.out"; then echo "ok: $p: run"
  else
    echo "FAIL: $p: run output differs, pass on vs off"
    diff "$TMP/$name.off.out" "$TMP/$name.on.out" | head -10; fail=1
  fi
done < "$RUNLIST"

if [ "$ncopies" -eq 0 ]; then echo "FAIL: no file has a \$spec_ copy: the pass did not run"; fail=1; fi
echo "spec-corpus-check: $nfiles files checked ($ncopies with copies), $nruns runs"
if [ "$fail" -eq 0 ]; then echo "spec-corpus-check: PASS"; else echo "spec-corpus-check: FAIL"; fi
exit "$fail"
