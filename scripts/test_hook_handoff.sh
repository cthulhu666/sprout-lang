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

# mk NAME HOOK MODE: repo $T/NAME with a local bare origin.
#   MODE own:      hooksPath is an outside dir with the real hook; the repo tracks a stub.
#   MODE relative: hooksPath is `.githooks`, holding the real hook.
#   MODE none:     hooksPath is an outside dir with the real hook; the repo has no hook.
mk() {
  local d="$T/$1" h="$2" mode="$3"
  rm -f "$CHECK_LOG" "$MARK" "$STDIN_LOG"
  git init -q -b master "$d" && git init -q --bare -b master "$T/$1.origin.git"
  cd "$d" || exit 1
  git remote add origin "$T/$1.origin.git"
  mkdir -p "$T/$1.outer" .githooks stdlib
  echo base >stdlib/prelude.sprout
  case "$mode" in
    own)
      cp "$ROOT/.githooks/$h" "$T/$1.outer/$h"
      # shellcheck disable=SC2016  # the stub expands these when it runs
      printf '#!/usr/bin/env bash\necho ran >>"$MARK"\ncat >>"$STDIN_LOG"\nexit 0\n' >".githooks/$h"
      chmod +x ".githooks/$h"
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
git commit -qm change 2>"$T/err"; rc=$?
check "commit allowed" 0 "$rc"
check "worktree copy ran" 1 "$(marks)"
check "outer logic did not run" 0 "$(count 'fmt_bin' "$T/err")"

echo "pre-commit: relative hooksPath runs once"
mk c2 pre-commit relative
echo x >stdlib/x.sprout; git add -A
git commit -qm change 2>"$T/err"; rc=$?
check "outer logic ran once" 1 "$(count 'fmt_bin' "$T/err")"
check "commit refused by it" 1 "$rc"

echo "pre-commit: no worktree copy falls through"
mk c3 pre-commit none
echo x >stdlib/x.sprout; git add -A
git commit -qm change 2>"$T/err"; rc=$?
check "outer logic ran" 1 "$(count 'fmt_bin' "$T/err")"
check "commit refused by it" 1 "$rc"

echo "pre-push: worktree copy takes over"
mk p1 pre-push own
echo x >stdlib/x.sprout; git add -A; git commit -qm change
git push -q origin feature 2>"$T/err"; rc=$?
check "push allowed" 0 "$rc"
check "worktree copy ran" 1 "$(marks)"
check "outer check did not run" 0 "$(runs)"
check "ref lines reached the worktree copy" 1 "$(count 'refs/heads/feature' "$STDIN_LOG")"

echo "pre-push: relative hooksPath runs once"
mk p2 pre-push relative
echo x >stdlib/x.sprout; git add -A; git commit -qm change
git push -q origin feature 2>"$T/err"; rc=$?
check "push allowed" 0 "$rc"
check "check ran once" 1 "$(runs)"

echo "pre-push: no worktree copy falls through"
mk p3 pre-push none
echo x >stdlib/x.sprout; git add -A; git commit -qm change
git push -q origin feature 2>"$T/err"; rc=$?
check "push allowed" 0 "$rc"
check "outer check ran" 1 "$(runs)"

exit $fail
