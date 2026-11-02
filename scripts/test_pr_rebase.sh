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
# check_ok WHAT CMD...: passes when CMD exits 0.
check_ok() {
  local what="$1"; shift
  if "$@" >/dev/null 2>&1; then check "$what" 0 0; else check "$what" 0 1; fi
}

T=$(mktemp -d /tmp/sprout_pr_rebase_test_XXXXXX)
trap 'rm -rf "$T"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# Stubs: line 1 of the "seed" is a fingerprint of stdlib/compiler/*.sprout, like
# seed-stale. Line 5 hashes stdlib/*.sprout (the prelude), which seed-stale cannot
# see; padding keeps a base edit of line 1 and a branch edit of line 5 from conflicting.
# When RESEED_LOG is set, a reseed appends the subject of HEAD to it.
cat >"$T/hash.sh" <<'EOF'
cat $(find "$1/stdlib/compiler" -name '*.sprout' | LC_ALL=C sort) | shasum -a 256 | cut -d' ' -f1
EOF
cat >"$T/reseed.sh" <<EOF
h=\$(bash "$T/hash.sh" .)
p=\$(cat stdlib/*.sprout | shasum -a 256 | cut -d' ' -f1)
printf '; seed-fingerprint: %s\n;\n;\n;\n; prelude: %s\n' "\$h" "\$p" > bootstrap/compile_driver.ll
[ -z "\${RESEED_LOG:-}" ] || git log -1 --format=%s >>"\$RESEED_LOG"
EOF
cat >"$T/check.sh" <<EOF
[ "\$(head -1 bootstrap/compile_driver.ll)" = "; seed-fingerprint: \$(bash "$T/hash.sh" .)" ]
EOF
export PR_REBASE_RESEED="bash $T/reseed.sh"
export PR_REBASE_SEED_CHECK="bash $T/check.sh"

# commit MSG FILE...: commit the named files, reseeding first when asked.
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
  ( cd "$1" && RESEED_LOG="$OUT.reseeded" bash "$SCRIPT" ) >"$OUT" 2>&1
  RC=$?
}

hash_at() {  # commit -> fingerprint of its compiler sources
  local x; x=$(mktemp -d "$T/arc_XXXXXX")
  git -C "$1" archive "$2" stdlib/compiler | tar -x -C "$x"
  bash "$T/hash.sh" "$x"
}

# Every compiler commit in base..HEAD carries a seed that matches its sources.
seeds_match() {
  local d="$1" c n=0 bad=0
  for c in $(git -C "$d" rev-list origin/master..HEAD); do
    git -C "$d" diff-tree --no-commit-id --name-only -r "$c" | grep -q '^stdlib/compiler/' || continue
    n=$((n + 1))
    [ "$(git -C "$d" show "$c:bootstrap/compile_driver.ll" | head -1)" = "; seed-fingerprint: $(hash_at "$d" "$c")" ] || bad=$((bad + 1))
  done
  echo "$n compiler commits, $bad stale"
}

head_seed_ok() {  # HEAD's seed line 1 matches HEAD's compiler sources
  [ "$(git -C "$1" show HEAD:bootstrap/compile_driver.ll | head -1)" = "; seed-fingerprint: $(hash_at "$1" HEAD)" ] && echo ok || echo stale
}

in_rebase() { ( cd "$1" && [ -d "$(git rev-parse --git-path rebase-merge)" ] ); }

echo "1. seed-only conflicts are reseeded"
mk seed seed
old=$(git -C "$T/seed" rev-parse feature)
run "$T/seed"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/seed" && echo yes || echo no)"
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
check "rebase left in place"        yes "$(in_rebase "$T/src" && echo yes || echo no)"
check "message names the file"      1 "$(grep -c 'stdlib/compiler/a.sprout' "$OUT")"
check "message says how to abort"   1 "$(grep -c 'git rebase --abort' "$OUT")"
git -C "$T/src" rebase --abort >/dev/null 2>&1

echo "3. runs from a linked worktree (.git is a file)"
mk wt seed
git -C "$T/wt" worktree add -q "$T/wt-linked" feature 2>/dev/null || git -C "$T/wt" worktree add -q "$T/wt-linked" -B feature2 feature
run "$T/wt-linked"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/wt-linked" && echo yes || echo no)"
check "every compiler commit seeded" "2 compiler commits, 0 stale" "$(seeds_match "$T/wt-linked")"

echo "4. a failing reseed fails the run"
mk fail stale
PR_REBASE_RESEED=false run "$T/fail"
check "non-zero exit"               1 "$((RC != 0))"
check "rebase left in place"        yes "$(in_rebase "$T/fail" && echo yes || echo no)"
git -C "$T/fail" rebase --abort >/dev/null 2>&1

echo "5. a dirty worktree is refused"
mk dirty seed
head=$(git -C "$T/dirty" rev-parse HEAD)
echo scribble >>"$T/dirty/other.txt"
run "$T/dirty"
check "non-zero exit"               1 "$((RC != 0))"
check "message says dirty"          1 "$(grep -c 'dirty' "$OUT")"
check "HEAD unchanged"              "$head" "$(git -C "$T/dirty" rev-parse HEAD)"
check "no rebase started"           no "$(in_rebase "$T/dirty" && echo yes || echo no)"
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
check "no rebase left behind"       no "$(in_rebase "$T/sepseed" && echo yes || echo no)"
check "based on origin/master"      "$(git -C "$T/sepseed" rev-parse origin/master)" "$(git -C "$T/sepseed" merge-base HEAD origin/master)"
check "two commits kept"            2 "$(git -C "$T/sepseed" rev-list --count origin/master..HEAD)"
check "compiler commit seeded"      "1 compiler commits, 0 stale" "$(seeds_match "$T/sepseed")"
check "HEAD seed matches sources"   ok "$(head_seed_ok "$T/sepseed")"
check "worktree clean"              "" "$(git -C "$T/sepseed" status --porcelain)"

echo "8. a commit with the seed but no compiler file is reseeded"
mk prelude prelude
run "$T/prelude"
check "exit 0"                      0 "$RC"
check "no rebase left behind"       no "$(in_rebase "$T/prelude" && echo yes || echo no)"
check "reseed ran for the commit"   1 "$(grep -cx p1 "$OUT.reseeded")"
check "HEAD seed matches sources"   ok "$(head_seed_ok "$T/prelude")"
check "worktree clean"              "" "$(git -C "$T/prelude" status --porcelain)"

exit $fail
