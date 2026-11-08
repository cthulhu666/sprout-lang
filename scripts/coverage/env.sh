# Sourced by every script in scripts/coverage/. Sets:
#   REPO     repo (or worktree) root
#   SCRIPTS  this directory
#   COV_DIR  all output; default build/coverage (gitignored). Absolute.
#   B        COV_DIR/build: instrumented binaries and objects
# Also exports LLVM_PROFILE_FILE into COV_DIR, so a stray instrumented run
# cannot drop default.profraw into the cwd. Each script overrides it per run.
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$SCRIPTS" rev-parse --show-toplevel)"
mkdir -p "${COV_DIR:-$REPO/build/coverage}"
COV_DIR="$(cd "${COV_DIR:-$REPO/build/coverage}" && pwd)"
B="$COV_DIR/build"
mkdir -p "$B" "$COV_DIR/stray"
export COV_DIR
export LLVM_PROFILE_FILE="$COV_DIR/stray/stray-%p.profraw"
# Sprout's IR carries `unknown-unknown-unknown`; give opt a real triple.
COV_TRIPLE="${COV_TRIPLE:-$(uname -m)-apple-macosx$(sw_vers -productVersion | cut -d. -f1).0.0}"
# Stage-1 swap bookkeeping (run_compiler_cov.sh).
STAGE1="$REPO/build/compile_driver_bin_stage1"
STAGE1_BACKUP="$COV_DIR/stage1.swap-backup"
