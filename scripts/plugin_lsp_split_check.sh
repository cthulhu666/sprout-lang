#!/usr/bin/env bash
# Gate: the JetBrains plugin's optional-LSP split, checked against shipped bytecode.
#
# The plugin has two layers. The language layer (highlighting, commenter, brace matching)
# must work in EVERY IntelliJ-based IDE. The LSP layer needs `com.intellij.modules.lsp`,
# which ships only in the commercial IDEs — so it is an OPTIONAL dependency, and its
# extensions live in sprout-lsp.xml, which an IDE without that module never loads.
#
# The split holds only while nothing in the language layer touches the LSP API. Break it
# and the plugin throws NoSuchClassError on load in IntelliJ IDEA Community or Android
# Studio — an IDE this project cannot test on every push.
#
# WHY NOT JUST RUN THE VERIFIER AGAINST COMMUNITY: it reports the missing package either
# way, and cannot tell "safely absent" from "will crash" — its own wording is "may be
# caused by absence of optional dependency". So a Community run is red whether the split
# is intact or broken, which makes it useless as a gate and corrosive as a habit.
#
# The discriminator is not WHETHER the package is referenced but FROM WHERE, and that is
# decidable from the bytecode: every referencing class must live under the LSP package.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_DIR="$ROOT/editors/intellij"
LSP_PACKAGE_PATH="dev/sprout/intellij/lsp/"
LSP_API="com/intellij/platform/lsp"

ZIP="$(ls -t "$PLUGIN_DIR"/build/distributions/*.zip 2>/dev/null | head -1)"
if [ -z "$ZIP" ]; then
  echo "ERROR: no plugin zip; run: just plugin-build" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

unzip -qq "$ZIP" -d "$WORK" || { echo "ERROR: could not unzip $ZIP" >&2; exit 1; }

CLASSES="$WORK/classes"
mkdir -p "$CLASSES"
jars=0
# EVERY jar, not the first one found: a plugin ships more than one (the settings page
# alone adds a searchableOptions jar), and any of them could carry an offending class.
# Picking one arbitrarily is how this check would silently start proving nothing.
while IFS= read -r jar; do
  jars=$((jars + 1))
  (cd "$CLASSES" && unzip -qqo "$jar" '*.class' 2>/dev/null) || true
done < <(find "$WORK" -name '*.jar')

if [ "$jars" -eq 0 ]; then
  echo "ERROR: no jar inside $ZIP" >&2
  exit 1
fi

total=0
referencing=0
offenders=""
while IFS= read -r class_file; do
  total=$((total + 1))
  rel="${class_file#"$CLASSES"/}"
  # The constant pool carries every referenced type, so -p (all members) plus -c
  # (disassemble) covers signatures and bodies alike.
  if javap -p -c -cp "$CLASSES" "${rel%.class}" 2>/dev/null | grep -q "$LSP_API"; then
    referencing=$((referencing + 1))
    case "$rel" in
      "$LSP_PACKAGE_PATH"*) ;;
      *) offenders="$offenders  $rel"$'\n' ;;
    esac
  fi
done < <(find "$CLASSES" -name '*.class')

if [ "$total" -eq 0 ]; then
  echo "ERROR: no classes found in the plugin jar — this check would pass vacuously" >&2
  exit 1
fi

# The other vacuous pass: if NOTHING references the LSP API, the plugin has no LSP layer
# and this gate is asserting nothing. Say so rather than printing a reassuring OK.
if [ "$referencing" -eq 0 ]; then
  echo "ERROR: no class references $LSP_API — the LSP layer is missing, or this check is" >&2
  echo "       looking at the wrong artifact. Refusing to report a vacuous pass." >&2
  exit 1
fi

if [ -n "$offenders" ]; then
  echo "plugin-split-check ✗ — classes OUTSIDE $LSP_PACKAGE_PATH reference $LSP_API:" >&2
  printf '%s' "$offenders" >&2
  echo "    These load in every IDE, including ones with no LSP module, so the plugin will" >&2
  echo "    fail with NoSuchClassError there. Move the code under the lsp package, or reach" >&2
  echo "    it only from an extension registered in sprout-lsp.xml." >&2
  exit 1
fi

echo "==> plugin-split-check: OK ($referencing of $total classes reference the LSP API, all under $LSP_PACKAGE_PATH)"
