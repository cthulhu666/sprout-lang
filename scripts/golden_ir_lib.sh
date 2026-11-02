# shellcheck shell=bash
# Shared helpers for the golden-IR scripts. Sourced, never executed.
# Sourced by: ir_golden_diff.sh, ir_golden_snapshot.sh, spec_gate.sh, spec_corpus_check.sh.

# <seconds> <command...>: run with a wall-clock limit; exit status 142 means it timed out.
limited() {
  local secs="$1"; shift
  perl -e 'alarm shift; exec @ARGV or exit 127' "$secs" "$@"
}

# <path> -> golden file name. Strips $REPO_ROOT/ (only when set and non-empty) and a leading ./,
# then joins every directory part with "__":
#   examples/foo.sprout -> examples__foo.sprout.ll
flatten_name() {
  local path="$1" rel dir_part base_part
  rel="$path"
  if [[ -n "${REPO_ROOT:-}" ]]; then rel="${rel#"$REPO_ROOT/"}"; fi
  rel="${rel#./}"
  dir_part="$(dirname "$rel")"
  base_part="$(basename "$rel")"
  echo "${dir_part//\//__}__${base_part}.ll"
}
