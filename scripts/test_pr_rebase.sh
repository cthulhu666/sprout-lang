#!/usr/bin/env bash
# Tests for scripts/pr_rebase.sh. Runs against throwaway repos with a local bare
# origin and stub reseed/check commands, never the caller's repo or toolchain.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/pr_rebase.sh"

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

T=$(mktemp -d /tmp/sprout_pr_rebase_test_XXXXXX)
trap 'rm -rf "$T"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# Stubs: line 1 of the "seed" is a fingerprint of stdlib/compiler/*.sprout, like
# seed-stale. Line 5 hashes stdlib/*.sprout (the prelude), which seed-stale cannot
# see; padding keeps a base edit of line 1 and a branch edit of line 5 from conflicting.
# When RESEED_LOG / CHECK_LOG is set, a reseed / check appends the subject of HEAD to it.
# When FAIL_ONCE names a file, the next reseed deletes it and fails.
cat >"$T/hash.sh" <<'EOF'
cat $(find "$1/stdlib/compiler" -name '*.sprout' | LC_ALL=C sort) | shasum -a 256 | cut -d' ' -f1
EOF
cat >"$T/reseed.sh" <<EOF
if [ -e "\${FAIL_ONCE:-}" ]; then rm -f "\$FAIL_ONCE"; exit 1; fi
h=\$(bash "$T/hash.sh" .)
p=\$(cat stdlib/*.sprout | shasum -a 256 | cut -d' ' -f1)
printf '; seed-fingerprint: %s\n;\n;\n;\n; prelude: %s\n' "\$h" "\$p" > bootstrap/compile_driver.ll
[ -z "\${RESEED_LOG:-}" ] || git log -1 --format=%s >>"\$RESEED_LOG"
EOF
cat >"$T/check.sh" <<EOF
[ -z "\${CHECK_LOG:-}" ] || git log -1 --format=%s >>"\$CHECK_LOG"
[ "\$(head -1 bootstrap/compile_driver.ll)" = "; seed-fingerprint: \$(bash "$T/hash.sh" .)" ]
EOF
export PR_REBASE_RESEED="bash $T/reseed.sh"
export PR_REBASE_SEED_CHECK="bash $T/check.sh"

# commit MSG: stage everything and commit.
commit() { git add -A && git commit -qm "$1"; }
reseed() { bash "$T/reseed.sh"; }

# mk NAME KIND: repo $T/NAME (branch `feature`, one commit past origin/master's
# old tip), then master moves on. KIND picks what each side changes.
mk() {
  local d="$T/$1" kind="$2"
  git init -q -b master "$d" && git init -q --bare -b master "$T/$1.origin.git"
  cd "$d" || exit 1
  git remote add origin "$T/$1.origin.git"
  mkdir -p stdlib/compiler bootstrap
  printf 'one\ntwo\nthree\n' >stdlib/compiler/a.sprout
  echo x >other.txt
  echo base >stdlib/prelude.sprout
  reseed
  commit init
  git push -q origin master
  git switch -q -c feature
  case $kind in
    seed)  # branch: compiler, plain, compiler. base: compiler.
      printf 'one\ntwo\nfeat\n' >stdlib/compiler/a.sprout; reseed; commit c1
      echo b >b.txt; commit c2
      echo c >stdlib/compiler/c.sprout; reseed; commit c3
      git switch -q master
      echo m >stdlib/compiler/m.sprout; reseed; commit base
      ;;
    src)  # both sides edit the same line of a compiler file
      printf 'one\ntwo\nfeat\n' >stdlib/compiler/a.sprout; reseed; commit c1
      git switch -q master
      printf 'one\ntwo\nbase\n' >stdlib/compiler/a.sprout; reseed; commit base
      ;;
    src2)  # a replayed compiler commit first, then the conflicting one
      echo d >stdlib/compiler/d.sprout; reseed; commit p0
      printf 'one\ntwo\nfeat\n' >stdlib/compiler/a.sprout; reseed; commit c1
      git switch -q master
      printf 'one\ntwo\nbase\n' >stdlib/compiler/a.sprout; reseed; commit base
      ;;
    okseed)  # seeded compiler commit, plain commit. base: a non-compiler file
      printf 'one\ntwo\nfeat\n' >stdlib/compiler/a.sprout; reseed; commit c1
      echo b >b.txt; commit c2
      git switch -q master
      echo d >other.txt; commit base
      ;;
    sepseed)  # branch: compiler commit, then a seed-only commit. base: compiler.
      printf 'one\ntwo\nfeat\n' >stdlib/compiler/a.sprout; commit c1
      echo feat >stdlib/prelude.sprout; reseed; commit seed  # survives the replay
      git switch -q master
      echo m >stdlib/compiler/m.sprout; reseed; commit base
      ;;
    prelude)  # branch: prelude + seed, no compiler file. base: compiler.
      echo feat >stdlib/prelude.sprout; reseed; commit p1
      git switch -q master
      echo m >stdlib/compiler/m.sprout; reseed; commit base
      ;;
    stale)  # branch forgot the seed; base moved a non-compiler file
      printf 'one\ntwo\nfeat\n' >stdlib/compiler/a.sprout; commit c1
      echo b >b.txt; commit c2
      git switch -q master
      echo d >other.txt; commit base
      ;;
  esac
  git push -q origin master
  git switch -q feature
  cd "$T" || exit 1
}

# run DIR: run the script in DIR; sets RC and OUT (file).
run() {
  OUT="$T/out.$RANDOM"
  ( cd "$1" && RESEED_LOG="$OUT.reseeded" CHECK_LOG="$OUT.checked" bash "$SCRIPT" ) >"$OUT" 2>&1
  RC=$?
}

hash_at() {  # commit -> fingerprint of its compiler sources
  local x; x=$(mktemp -d "$T/arc_XXXXXX")
  git -C "$1" archive "$2" stdlib/compiler | tar -x -C "$x"
  bash "$T/hash.sh" "$x"
}

# Every compiler commit in base..HEAD carries a seed that matches its sources.
seeds_match() {
  local d="$1" c n=0 bad=0 files
  for c in $(git -C "$d" rev-list origin/master..HEAD); do
    files="$(git -C "$d" diff-tree --no-commit-id --name-only -r "$c")" \
      && grep -q '^stdlib/compiler/' <<<"$files" || continue
    n=$((n + 1))
    [ "$(git -C "$d" show "$c:bootstrap/compile_driver.ll" | head -1)" = "; seed-fingerprint: $(hash_at "$d" "$c")" ] || bad=$((bad + 1))
  done
  echo "$n compiler commits, $bad stale"
}

head_seed_ok() {  # HEAD's seed line 1 matches HEAD's compiler sources
  [ "$(git -C "$1" show HEAD:bootstrap/compile_driver.ll | head -1)" = "; seed-fingerprint: $(hash_at "$1" HEAD)" ] && echo ok || echo stale
}

in_rebase() {  # DIR -> yes | no
  if ( cd "$1" && [ -d "$(git rev-parse --git-path rebase-merge)" ] ); then echo yes; else echo no; fi
}

# resolve DIR: settle the `src` conflict by hand (both files), staged.
resolve() {
  ( cd "$1" && printf 'one\ntwo\nmerged\n' >stdlib/compiler/a.sprout &&
    git checkout -q --theirs -- bootstrap/compile_driver.ll && git add -A )
}

echo "1. seed-only conflicts are reseeded"
mk seed seed
old=$(git -C "$T/seed" rev-parse feature)
run "$T/seed"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/seed")"
check "based on origin/master"      "$(git -C "$T/seed" rev-parse origin/master)" "$(git -C "$T/seed" merge-base HEAD origin/master)"
check "every compiler commit seeded" "2 compiler commits, 0 stale" "$(seeds_match "$T/seed")"
check "plain commit touches only its file" "b.txt" \
  "$(git -C "$T/seed" diff-tree --no-commit-id --name-only -r HEAD~1)"
check "three commits kept"          3 "$(git -C "$T/seed" rev-list --count origin/master..HEAD)"
check "worktree clean"              "" "$(git -C "$T/seed" status --porcelain)"
check "push command leases old head" 1 \
  "$(grep -cxF "git push --force-with-lease=feature:$old origin feature" "$OUT")"
check "old head printed"            1 "$(( $(grep -cF "$old" "$OUT") > 0 ))"
check "branch not pushed"           "$(git -C "$T/seed" rev-parse origin/master)" \
  "$(git --git-dir="$T/seed.origin.git" rev-parse master)"

echo "2. conflict outside the seed stops the rebase"
mk src src
run "$T/src"
check "non-zero exit"               1 "$((RC != 0))"
check "rebase left in place"        yes "$(in_rebase "$T/src")"
check "message names the file"      1 "$(grep -c 'stdlib/compiler/a.sprout' "$OUT")"
check "message says how to abort"   1 "$(grep -c 'git rebase --abort' "$OUT")"
check "message says to rerun"       1 "$(grep -c 'just pr-rebase' "$OUT")"
git -C "$T/src" rebase --abort >/dev/null 2>&1

echo "3. runs from a linked worktree (.git is a file)"
mk wt seed
git -C "$T/wt" worktree add -q "$T/wt-linked" feature 2>/dev/null || git -C "$T/wt" worktree add -q "$T/wt-linked" -B feature2 feature
run "$T/wt-linked"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/wt-linked")"
check "every compiler commit seeded" "2 compiler commits, 0 stale" "$(seeds_match "$T/wt-linked")"

echo "4. a failing reseed fails the run"
mk fail stale
PR_REBASE_RESEED=false run "$T/fail"
check "non-zero exit"               1 "$((RC != 0))"
check "rebase left in place"        yes "$(in_rebase "$T/fail")"
git -C "$T/fail" rebase --abort >/dev/null 2>&1

echo "5. a dirty worktree is refused"
mk dirty seed
head=$(git -C "$T/dirty" rev-parse HEAD)
echo scribble >>"$T/dirty/other.txt"
run "$T/dirty"
check "non-zero exit"               1 "$((RC != 0))"
check "message says dirty"          1 "$(grep -c 'dirty' "$OUT")"
check "HEAD unchanged"              "$head" "$(git -C "$T/dirty" rev-parse HEAD)"
check "no rebase started"           no "$(in_rebase "$T/dirty")"
check "edit kept"                   scribble "$(tail -1 "$T/dirty/other.txt")"

echo "6. a stale seed on a clean replay is amended"
mk stale stale
run "$T/stale"
check "exit 0"                      0 "$RC"
check "compiler commit now seeded"  "1 compiler commits, 0 stale" "$(seeds_match "$T/stale")"
check "two commits kept"            2 "$(git -C "$T/stale" rev-list --count origin/master..HEAD)"
check "worktree clean"              "" "$(git -C "$T/stale" status --porcelain)"

echo "7. a seed-only commit after a compiler commit is not an error"
mk sepseed sepseed
run "$T/sepseed"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/sepseed")"
check "based on origin/master"      "$(git -C "$T/sepseed" rev-parse origin/master)" "$(git -C "$T/sepseed" merge-base HEAD origin/master)"
check "two commits kept"            2 "$(git -C "$T/sepseed" rev-list --count origin/master..HEAD)"
check "compiler commit seeded"      "1 compiler commits, 0 stale" "$(seeds_match "$T/sepseed")"
check "HEAD seed matches sources"   ok "$(head_seed_ok "$T/sepseed")"
check "worktree clean"              "" "$(git -C "$T/sepseed" status --porcelain)"

echo "8. a commit with the seed but no compiler file is reseeded"
mk prelude prelude
run "$T/prelude"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/prelude")"
check "reseed ran for the commit"   1 "$(grep -cx p1 "$OUT.reseeded")"
check "HEAD seed matches sources"   ok "$(head_seed_ok "$T/prelude")"
check "worktree clean"              "" "$(git -C "$T/prelude" status --porcelain)"

echo "9. a rerun after resolving a conflict resumes and finishes"
mk resume src2
git -C "$T/resume" push -q origin feature
lease=$(git -C "$T/resume" rev-parse origin/feature)
run "$T/resume"
check "first run stops"             yes "$(in_rebase "$T/resume")"
prev=$(git -C "$T/resume" rev-parse HEAD)
resolve "$T/resume"
run "$T/resume"
check "previous commit not amended" "$prev" "$(git -C "$T/resume" rev-parse HEAD~1)"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/resume")"
check "based on origin/master"      "$(git -C "$T/resume" rev-parse origin/master)" "$(git -C "$T/resume" merge-base HEAD origin/master)"
check "seed check ran"              1 "$(grep -cx c1 "$OUT.checked")"
check "HEAD seed matches sources"   ok "$(head_seed_ok "$T/resume")"
check "push line has the original lease" 1 \
  "$(grep -cxF "git push --force-with-lease=feature:$lease origin feature" "$OUT")"
check "worktree clean"              "" "$(git -C "$T/resume" status --porcelain)"

echo "10. a rerun after a manual continue prints the remote's lease"
mk manual src
git -C "$T/manual" push -q origin feature
lease=$(git -C "$T/manual" rev-parse origin/feature)
run "$T/manual"
resolve "$T/manual"
( cd "$T/manual" && GIT_EDITOR=true git rebase --continue ) >/dev/null 2>&1
check "manual continue finished"    no "$(in_rebase "$T/manual")"
run "$T/manual"
check "exit 0"                      0 "$RC"
check "final checks ran"            1 "$(grep -c '^rebased:' "$OUT")"
check "lease is origin/feature, not HEAD" 1 \
  "$(grep -cxF "git push --force-with-lease=feature:$lease origin feature" "$OUT")"
check "local head differs from lease" 1 \
  "$([ "$(git -C "$T/manual" rev-parse HEAD)" != "$lease" ] && echo 1 || echo 0)"

echo "11. a rebase this script did not start is refused"
mk foreign src
git -C "$T/foreign" rebase origin/master >/dev/null 2>&1
check "foreign rebase in progress"  yes "$(in_rebase "$T/foreign")"
run "$T/foreign"
check "non-zero exit"               1 "$((RC != 0))"
check "rebase untouched"            yes "$(in_rebase "$T/foreign")"
check "message says so"             1 "$(grep -c 'did not start' "$OUT")"
git -C "$T/foreign" rebase --abort >/dev/null 2>&1

echo "12. a rerun after a failed step re-runs that step"
mk redo okseed
touch "$T/failonce"
FAIL_ONCE="$T/failonce" run "$T/redo"
check "first run stops"             yes "$(in_rebase "$T/redo")"
check "first run exits non-zero"    1 "$((RC != 0))"
run "$T/redo"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/redo")"
check "reseed ran again for c1"     1 "$(grep -cx c1 "$OUT.reseeded")"
check "seed check ran for c1"       1 "$(grep -cx c1 "$OUT.checked")"
check "compiler commit seeded"      "1 compiler commits, 0 stale" "$(seeds_match "$T/redo")"
check "HEAD seed matches sources"   ok "$(head_seed_ok "$T/redo")"
check "two commits kept"            2 "$(git -C "$T/redo" rev-list --count origin/master..HEAD)"
check "worktree clean"              "" "$(git -C "$T/redo" status --porcelain)"

exit $fail
