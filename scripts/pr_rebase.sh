#!/usr/bin/env bash
# Rebase the current branch onto origin/master, reseeding every commit that
# touches stdlib/compiler/ or the seed. Stops on any other conflict or a failed step.
# Never pushes. Run it as `just pr-rebase`. After a stop, fix the cause and run it
# again: it resumes its own rebase, then runs the final checks.
#
# Overrides (the test stubs them):
#   PR_REBASE_RESEED      default: mise exec -- just refresh-seed-clean
#   PR_REBASE_SEED_CHECK  default: mise exec -- just seed-stale
# Internal mode, run by `git rebase -x` after each picked commit:
#   pr_rebase.sh step <logdir>
set -u
export GIT_EDITOR=true
RESEED=${PR_REBASE_RESEED:-mise exec -- just refresh-seed-clean}
SEED_CHECK=${PR_REBASE_SEED_CHECK:-mise exec -- just seed-stale}
SEED=bootstrap/compile_driver.ll
SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")

say() { echo "$*"; echo "$*" >>"$LOG/summary.txt"; }

reseed() {  # refresh-seed-clean removes stage1 itself; a stale stage2 would survive it
  rm -f build/compile_driver_bin_stage2
  bash -c "$RESEED" >>"$LOG/reseed.log" 2>&1
}

if [ "${1:-}" = step ]; then
  LOG=$2
  # The author's own seed marks a commit that needs one, even with no compiler file in it.
  if git diff-tree --quiet HEAD^ HEAD -- stdlib/compiler "$SEED"; then rm -f "$LOG/fresh"; exit 0; fi
  say "step $(git log -1 --format='%h %s')"
  if [ -e "$LOG/fresh" ]; then
    rm -f "$LOG/fresh"  # the conflict path reseeded this commit already
  else
    reseed || { say "  reseed FAILED"; exit 1; }
    if ! git diff --quiet -- "$SEED"; then
      git add "$SEED"
      git commit --amend --no-edit >>"$LOG/reseed.log" 2>&1 || { say "  amend FAILED"; exit 1; }
      say "  seed amended"
    fi
  fi
  bash -c "$SEED_CHECK" >>"$LOG/reseed.log" 2>&1 || { say "  seed check FAILED"; exit 1; }
  exit 0
fi

LOG=$(git rev-parse --absolute-git-dir)/pr-rebase || exit 2
STATE=$LOG/state  # three lines: branch, base, lease sha
mkdir -p "$LOG"
RB=$(git rev-parse --git-path rebase-merge)
in_rebase() { [ -d "$RB" ]; }

ABORT="git rebase --abort"
rc=0
if in_rebase; then
  # Resume only a rebase this script started: same branch, same onto.
  { read -r BRANCH; read -r BASE; read -r OLD; } 2>/dev/null <"$STATE"
  if [ -z "${OLD:-}" ] || [ "$(cat "$RB/head-name")" != "refs/heads/$BRANCH" ] \
    || [ "$(cat "$RB/onto")" != "$BASE" ]; then
    echo "a rebase is in progress that this script did not start: finish it or run '$ABORT'"
    exit 2
  fi
  say "resuming: branch $BRANCH, lease $OLD, base $BASE"
  # Unmerged paths go through the loop below. Otherwise the user's work is done.
  if [ -z "$(git diff --name-only --diff-filter=U)" ]; then
    # git does not re-run a failed exec, so run our step for HEAD first. After a
    # conflict stop HEAD is the previous commit: the step would amend into it.
    if git diff --cached --quiet && tail -n 1 "$RB/done" | grep -q '^exec '; then
      say "re-running the failed step"
      bash "$SELF" step "$LOG" || {
        say "the step failed again (see $LOG/summary.txt and reseed.log)."
        say "fix it, then run 'just pr-rebase' again (or '$ABORT')."
        exit 1
      }
    fi
    git rebase --continue >>"$LOG/git.log" 2>&1
    rc=$?
  fi
else
  : >"$LOG/summary.txt"; : >"$LOG/reseed.log"; rm -f "$LOG/fresh"
  BRANCH=$(git symbolic-ref --short -q HEAD) || { say "detached HEAD: check out the branch first"; exit 2; }
  [ -z "$(git status --porcelain)" ] || { say "worktree dirty: commit or stash first"; exit 2; }
  git fetch origin >>"$LOG/git.log" 2>&1 || { say "fetch FAILED (see $LOG/git.log)"; exit 2; }
  BASE=$(git rev-parse origin/master) || exit 2
  # The lease is what the remote has, so a rerun on a rebased branch still names it.
  OLD=$(git rev-parse -q --verify "refs/remotes/origin/$BRANCH" || git rev-parse HEAD)
  printf '%s\n%s\n%s\n' "$BRANCH" "$BASE" "$OLD" >"$STATE"
  say "branch $BRANCH, lease $OLD, base $BASE"
  say "logs in $LOG"
  git rebase -x "bash '$SELF' step '$LOG'" origin/master >>"$LOG/git.log" 2>&1
  rc=$?
fi
while in_rebase; do
  U=$(git diff --name-only --diff-filter=U)
  if [ -z "$U" ]; then
    say "a step failed (see $LOG/summary.txt and reseed.log)."
    say "fix it, then run 'just pr-rebase' again (or '$ABORT')."
    exit 1
  fi
  OTHER=$(echo "$U" | grep -vxF "$SEED")
  if [ -n "$OTHER" ]; then
    say "conflict outside the seed: $(echo $OTHER)"
    say "resolve, 'git add', then run 'just pr-rebase' again (or '$ABORT')."
    exit 1
  fi
  say "seed conflict at $(git rev-parse --short REBASE_HEAD), reseeding"
  git checkout --theirs -- "$SEED"
  reseed || { say "  reseed FAILED (see $LOG/reseed.log); run '$ABORT' to undo"; exit 1; }
  git add "$SEED"
  touch "$LOG/fresh"
  git rebase --continue >>"$LOG/git.log" 2>&1
  rc=$?
done
[ $rc -eq 0 ] || { say "rebase FAILED (see $LOG/git.log)"; exit 1; }

[ "$(git merge-base HEAD origin/master)" = "$BASE" ] || { say "result is not based on $BASE"; exit 1; }
# A dropped seed commit would leave seed-stale green on a stale tree. A seed-only
# commit is fine, so the check is C within B, not equality.
C=$(git log --format=%h "$BASE..HEAD" -- stdlib/compiler)
B=$(git log --format=%h "$BASE..HEAD" -- "$SEED")
MISSING=$(for c in $C; do echo "$B" | grep -qx "$c" || echo "$c"; done)
[ -z "$MISSING" ] || { say "compiler commits without a seed: $(echo $MISSING)"; exit 1; }
[ -z "$(git status --porcelain)" ] || { say "worktree dirty after rebase"; exit 1; }

rm -f "$STATE"
say "rebased: head $(git rev-parse HEAD), seed commits: $(echo $B)"
echo "next: mise exec -- just dod"
echo "then push (not done here):"
echo "git push --force-with-lease=$BRANCH:$OLD origin $BRANCH"
