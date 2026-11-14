#!/usr/bin/env bash
# Tests for .githooks/pre-push. Runs real `git push` against throwaway repos with a
# local bare origin and a stub check (SPROUT_PRE_PUSH_CHECK), never the caller's repo.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/.githooks/pre-push"

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

if [ ! -x "$HOOK" ]; then
  printf '  FAIL hook missing or not executable: %s\n' "$HOOK" >&2
  exit 1
fi

T=$(mktemp -d /tmp/sprout_pre_push_test_XXXXXX)
trap 'rm -rf "$T"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# The stub check appends a line to CHECK_LOG and fails while the file CHECK_FAIL exists.
export CHECK_LOG="$T/check.log" CHECK_FAIL="$T/check.fail"
# shellcheck disable=SC2016  # expanded by the hook's bash -c, not here
export SPROUT_PRE_PUSH_CHECK='echo ran >>"$CHECK_LOG"; [ ! -e "$CHECK_FAIL" ]'

mkdir "$T/hooks"
cp "$HOOK" "$T/hooks/pre-push"

commit() { git add -A && git commit -qm "$1"; }
runs() { if [ -e "$CHECK_LOG" ]; then wc -l <"$CHECK_LOG" | tr -d ' '; else echo 0; fi; }
remote_ref() { git --git-dir="$T/$1.origin.git" rev-parse -q --verify "refs/heads/$2" || echo none; }

# mk NAME: repo $T/NAME on branch `feature`, one commit past master; master is on origin.
mk() {
  local d="$T/$1"
  rm -f "$CHECK_LOG" "$CHECK_FAIL"
  git init -q -b master "$d" && git init -q --bare -b master "$T/$1.origin.git"
  cd "$d" || exit 1
  git config core.hooksPath "$T/hooks"
  git remote add origin "$T/$1.origin.git"
  mkdir -p stdlib bootstrap docs
  echo base >stdlib/prelude.sprout
  echo base >bootstrap/compile_driver.ll
  echo base >docs/a.md
  commit init
  git push -q --no-verify origin master
  git switch -q -c feature
}

echo "a. docs-only new branch"
mk a
echo d >docs/b.md; commit docs
git push -q origin feature 2>"$T/err"
check "check not run" 0 "$(runs)"
check "push landed" "$(git rev-parse HEAD)" "$(remote_ref a feature)"

echo "b. stdlib change, check passes"
mk b
echo x >stdlib/x.sprout; commit stdlib
git push -q origin feature 2>"$T/err"
check "check ran once" 1 "$(runs)"
check "push landed" "$(git rev-parse HEAD)" "$(remote_ref b feature)"

echo "c. stdlib change, check fails"
mk c
echo x >stdlib/x.sprout; commit stdlib
touch "$CHECK_FAIL"
git push -q origin feature 2>"$T/err"
check "check ran" 1 "$(runs)"
check "push rejected" none "$(remote_ref c feature)"
check "message says reseed" 1 "$(grep -c 'refresh-seed' "$T/err")"

echo "d. non-HEAD branch touching stdlib"
mk d
git switch -q -c other
echo x >stdlib/x.sprout; commit stdlib
git switch -q master
git push -q origin other 2>"$T/err"
check "check not run" 0 "$(runs)"
check "push rejected" none "$(remote_ref d other)"
check "message names the ref" 1 "$(grep -c 'refs/heads/other' "$T/err")"

echo "e. HEAD pushed, tracked stdlib file modified"
mk e
echo x >stdlib/x.sprout; commit stdlib
echo dirty >stdlib/prelude.sprout
git push -q origin feature 2>"$T/err"
check "check not run" 0 "$(runs)"
check "push rejected" none "$(remote_ref e feature)"
check "message names the ref" 1 "$(grep -c 'refs/heads/feature' "$T/err")"

echo "f. branch delete"
mk f
echo d >docs/b.md; commit docs
git push -q origin feature 2>"$T/err"
git push -q origin :feature 2>"$T/err"
check "check not run" 0 "$(runs)"
check "delete landed" none "$(remote_ref f feature)"

echo "g. rebase onto a master with a prelude change"
mk g
echo d >docs/b.md; commit docs
git push -q origin feature 2>"$T/err"
check "docs-only push skipped the check" 0 "$(runs)"
git switch -q master
echo newer >stdlib/prelude.sprout; commit prelude
git push -q --no-verify origin master
git switch -q feature
git rebase -q master
git push -q --force-with-lease origin feature 2>"$T/err"
check "check ran" 1 "$(runs)"
check "push landed" "$(git rev-parse HEAD)" "$(remote_ref g feature)"

echo "h. seed-only change"
mk h
echo new >bootstrap/compile_driver.ll; commit seed
git push -q origin feature 2>"$T/err"
check "check ran once" 1 "$(runs)"
check "push landed" "$(git rev-parse HEAD)" "$(remote_ref h feature)"

echo "i. HEAD pushed, untracked stdlib module"
mk i
mkdir stdlib/compiler
echo x >stdlib/compiler/a.sprout; commit compiler
echo new >stdlib/compiler/new.sprout
git push -q origin feature 2>"$T/err"
check "check not run" 0 "$(runs)"
check "push rejected" none "$(remote_ref i feature)"
check "message names the ref" 1 "$(grep -c 'refs/heads/feature' "$T/err")"
rm stdlib/compiler/new.sprout
git push -q origin feature 2>"$T/err"
check "nested stdlib change ran the check" 1 "$(runs)"
check "push landed" "$(git rev-parse HEAD)" "$(remote_ref i feature)"

echo "j. annotated tag of HEAD touching stdlib"
mk j
echo x >stdlib/x.sprout; commit stdlib
git tag -a v1 -m v1
git push -q origin v1 2>"$T/err"
check "check ran once" 1 "$(runs)"
check "tag landed" "$(git rev-parse v1)" \
  "$(git --git-dir="$T/j.origin.git" rev-parse -q --verify refs/tags/v1 || echo none)"

exit $fail
