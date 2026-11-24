#!/usr/bin/env bash
# Tests the worktree hand-off in .githooks/pre-commit and .githooks/pre-push. Runs real
# `git commit` / `git push` in throwaway repos, never the caller's repo.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

fail=0
check() {
  local what="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    printf '  ok   %s\n' "$what"
  else
    printf '  FAIL %s: wanted [%s], got [%s]\n' "$what" "$want" "$got" >&2
    fail=1
  fi
}

for h in pre-commit pre-push; do
  if [ ! -x "$ROOT/.githooks/$h" ]; then
    printf '  FAIL hook missing or not executable: %s\n' "$ROOT/.githooks/$h" >&2
    exit 1
  fi
done

T=$(mktemp -d /tmp/sprout_handoff_test_XXXXXX)
trap 'rm -rf "$T"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# The stub pre-push check counts runs in CHECK_LOG; a worktree stub writes MARK.
export CHECK_LOG="$T/check.log" MARK="$T/mark" STDIN_LOG="$T/stdin"
# shellcheck disable=SC2016  # expanded by the hook's bash -c, not here
export SPROUT_PRE_PUSH_CHECK='echo ran >>"$CHECK_LOG"'

runs() { if [ -e "$CHECK_LOG" ]; then wc -l <"$CHECK_LOG" | tr -d ' '; else echo 0; fi; }
marks() { if [ -e "$MARK" ]; then wc -l <"$MARK" | tr -d ' '; else echo 0; fi; }
count() { grep -c "$1" "$2" 2>/dev/null || true; }

# run_bounded SECS CMD...: run CMD; after SECS kill its whole process group and return 124.
# The group kill reaches a looping hook, which outlives a killed git. Stock macOS has no `timeout`.
run_bounded() {
  local secs="$1" pid dog rc
  shift
  set -m  # each background job gets its own process group
  "$@" &
  pid=$!
  (sleep "$secs"; kill -9 -- "-$pid") >/dev/null 2>&1 &
  dog=$!
  set +m
  wait "$pid" 2>/dev/null; rc=$?
  kill -- "-$dog" 2>/dev/null; wait "$dog" 2>/dev/null
  if [ "$rc" -eq 137 ]; then return 124; fi
  return "$rc"
}
BOUND=30

# mk NAME HOOK MODE: repo $T/NAME with a local bare origin.
#   MODE own:      hooksPath is an outside dir with the real hook; the repo tracks a stub.
#   MODE relative: hooksPath is `.githooks`, holding the real hook.
#   MODE ownfail:  like own, but the stub exits 1.
#   MODE ownnox:   like own, but the stub is not executable.
#   MODE none:    hooksPath is an outside dir with the real hook; the repo has no hook.
mk() {
  local d="$T/$1" h="$2" mode="$3"
  rm -f "$CHECK_LOG" "$MARK" "$STDIN_LOG"
  git init -q -b master "$d" && git init -q --bare -b master "$T/$1.origin.git"
  cd "$d" || exit 1
  git remote add origin "$T/$1.origin.git"
  mkdir -p "$T/$1.outer" .githooks stdlib
  echo base >stdlib/prelude.sprout
  case "$mode" in
    own | ownfail | ownnox)
      local code=0
      [ "$mode" = ownfail ] && code=1
      cp "$ROOT/.githooks/$h" "$T/$1.outer/$h"
      # shellcheck disable=SC2016  # the stub expands these when it runs
      printf '#!/usr/bin/env bash\necho ran >>"$MARK"\ncat >>"$STDIN_LOG"\nexit %s\n' "$code" >".githooks/$h"
      if [ "$mode" = ownnox ]; then chmod -x ".githooks/$h"; else chmod +x ".githooks/$h"; fi
      git config core.hooksPath "$T/$1.outer"
      ;;
    relative)
      cp "$ROOT/.githooks/$h" ".githooks/$h"
      git config core.hooksPath .githooks
      ;;
    none)
      cp "$ROOT/.githooks/$h" "$T/$1.outer/$h"
      rmdir .githooks
      git config core.hooksPath "$T/$1.outer"
      ;;
  esac
  git add -A && git commit -qm init --no-verify
  git push -q --no-verify origin master
  git switch -q -c feature
}

echo "pre-commit: worktree copy takes over"
mk c1 pre-commit own
echo x >stdlib/x.sprout; git add -A
run_bounded "$BOUND" git commit -qm change 2>"$T/err"; rc=$?
check "commit allowed" 0 "$rc"
check "worktree copy ran" 1 "$(marks)"
check "outer logic did not run" 0 "$(count 'fmt_bin' "$T/err")"

echo "pre-commit: relative hooksPath runs once"
mk c2 pre-commit relative
echo x >stdlib/x.sprout; git add -A
run_bounded "$BOUND" git commit -qm change 2>"$T/err"; rc=$?
check "outer logic ran once" 1 "$(count 'fmt_bin' "$T/err")"
check "commit refused by it" 1 "$rc"

echo "pre-commit: no worktree copy falls through"
mk c3 pre-commit none
echo x >stdlib/x.sprout; git add -A
run_bounded "$BOUND" git commit -qm change 2>"$T/err"; rc=$?
check "outer logic ran" 1 "$(count 'fmt_bin' "$T/err")"
check "commit refused by it" 1 "$rc"

echo "pre-push: worktree copy takes over"
mk p1 pre-push own
echo x >stdlib/x.sprout; git add -A; git commit -qm change
run_bounded "$BOUND" git push -q origin feature 2>"$T/err"; rc=$?
check "push allowed" 0 "$rc"
check "worktree copy ran" 1 "$(marks)"
check "outer check did not run" 0 "$(runs)"
check "ref lines reached the worktree copy" 1 "$(count 'refs/heads/feature' "$STDIN_LOG")"

echo "pre-push: relative hooksPath runs once"
mk p2 pre-push relative
echo x >stdlib/x.sprout; git add -A; git commit -qm change
run_bounded "$BOUND" git push -q origin feature 2>"$T/err"; rc=$?
check "push allowed" 0 "$rc"
check "check ran once" 1 "$(runs)"

echo "pre-push: no worktree copy falls through"
mk p3 pre-push none
echo x >stdlib/x.sprout; git add -A; git commit -qm change
run_bounded "$BOUND" git push -q origin feature 2>"$T/err"; rc=$?
check "push allowed" 0 "$rc"
check "outer check ran" 1 "$(runs)"

echo "pre-commit: failing worktree copy refuses the commit"
mk c4 pre-commit ownfail
echo x >stdlib/x.sprout; git add -A
run_bounded "$BOUND" git commit -qm change 2>"$T/err"; rc=$?
check "commit refused" 1 "$rc"
check "worktree copy ran" 1 "$(marks)"

echo "pre-commit: non-executable worktree copy falls through"
mk c5 pre-commit ownnox
echo x >stdlib/x.sprout; git add -A
run_bounded "$BOUND" git commit -qm change 2>"$T/err"; rc=$?
check "outer logic ran" 1 "$(count 'fmt_bin' "$T/err")"
check "worktree copy did not run" 0 "$(marks)"
check "no permission error" 0 "$(count 'Permission denied' "$T/err")"
check "not refused with 126" 1 "$rc"

echo "pre-push: failing worktree copy refuses the push"
mk p4 pre-push ownfail
echo x >stdlib/x.sprout; git add -A; git commit -qm change
run_bounded "$BOUND" git push -q origin feature 2>"$T/err"; rc=$?
check "push refused" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
check "worktree copy ran" 1 "$(marks)"

echo "pre-push: non-executable worktree copy falls through"
mk p5 pre-push ownnox
echo x >stdlib/x.sprout; git add -A; git commit -qm change
run_bounded "$BOUND" git push -q origin feature 2>"$T/err"; rc=$?
check "push allowed" 0 "$rc"
check "outer check ran" 1 "$(runs)"
check "worktree copy did not run" 0 "$(marks)"
check "no permission error" 0 "$(count 'Permission denied' "$T/err")"

echo "watchdog: a looping hook fails instead of hanging"
mk wl pre-commit own
# shellcheck disable=SC2016  # the stub expands these when it runs
printf '#!/usr/bin/env bash\nexec "$0" "$@"\n' >.githooks/pre-commit
echo x >stdlib/x.sprout; git add -A
run_bounded 2 git commit -qm change 2>"$T/err"; rc=$?
check "killed with 124" 124 "$rc"
check "no hook left running" 0 "$(pgrep -f "$T/wl/" | wc -l | tr -d ' ')"

exit $fail
