#!/usr/bin/env bash
# Gate: the `sproutd --lsp` transport, driven with real framed JSON-RPC messages.
#
# The server's pure helpers were unit-tested, but nothing exercised the TRANSPORT before
# this script — no test, no recipe ran `--lsp`. Its protocol behaviour was known only by
# hand-driving the binary, which is how it came to advertise two capabilities it does not
# implement (see the honesty check below).
#
# Three things only a round-trip can show, which is why this is a script and not a
# `.spr` test (the pure helpers stay in tests/stdlib/compiler/test_lsp_driver.spr):
#
#   1. Content-Length framing — the server writes byte counts, and a mismatch desyncs
#      the stream rather than producing a wrong answer.
#   2. Diagnostic line/column, which is what the editor draws a squiggle under.
#   3. CAPABILITY HONESTY: every capability in the initialize result must actually
#      answer. A server that advertises `hoverProvider` and returns null gives the
#      client a broken feature rather than an absent one.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPROUTD="$ROOT/build/sproutd"
STDLIB="$ROOT/stdlib"
fail=0

if [ ! -x "$SPROUTD" ]; then
  echo "ERROR: $SPROUTD not found; run: just build-sproutd" >&2
  exit 1
fi

# Frame each line of stdin as one LSP message. `length` is characters, not bytes, so
# every fixture below stays ASCII — a non-ASCII fixture would need byte length here.
frame() { awk '{printf "Content-Length: %d\r\n\r\n%s", length($0), $0}'; }

# Drive the server with the given messages and return one JSON response per line.
# Splitting on the header is what separates frames: they arrive concatenated, with no
# newline between one body and the next header.
drive() {
  printf '%s\n' "$@" \
    | frame \
    | "$SPROUTD" --lsp "$STDLIB" 2>/dev/null \
    | tr -d '\r' \
    | awk '{gsub(/Content-Length: [0-9]+/, "\n"); print}' \
    | grep '^{'
}

# Same, with an extra package root registered.
drive_with_roots() {
  local pkg_root="$1"
  shift
  printf '%s\n' "$@" \
    | frame \
    | "$SPROUTD" --lsp "$STDLIB" --package-root "$pkg_root" 2>/dev/null \
    | tr -d '\r' \
    | awk '{gsub(/Content-Length: [0-9]+/, "\n"); print}' \
    | grep '^{'
}

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

open_doc() { # uri, text
  printf '{"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{"uri":"%s","text":"%s"}}}' "$1" "$2"
}
change_doc() { # uri, text
  printf '{"jsonrpc":"2.0","method":"textDocument/didChange","params":{"textDocument":{"uri":"%s"},"contentChanges":[{"text":"%s"}]}}' "$1" "$2"
}

check() { # label, condition-already-evaluated (0 = pass)
  if [ "$2" -eq 0 ]; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi
}

URI="file:///tmp/lsp_smoke.sprout"
GOOD='module main\n\nlet answer = 42\n'
# `nope` is undefined; it starts at line 2 (0-based), character 13.
BAD='module main\n\nlet answer = nope\n'

# --- 1. initialize ---------------------------------------------------------------
resp="$(drive "$INIT")"
echo "$resp" | grep -q '"id":1' && echo "$resp" | grep -q '"capabilities"'
check "initialize returns capabilities" $?
echo "$resp" | grep -q '"serverInfo"'
check "initialize identifies the server" $?

# Non-vacuity: if the server said nothing at all, every grep above would also have
# failed, and a reader could mistake that for a protocol assertion. Say it out loud.
[ "$(echo "$resp" | grep -c '^{')" -ge 1 ]
check "the server produced at least one framed response" $?

# --- 2. diagnostics --------------------------------------------------------------
resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")")"
echo "$resp" | grep -q '"diagnostics":\[\]'
check "a clean document reports no diagnostics" $?

resp="$(drive "$INIT" "$(open_doc "$URI" "$BAD")")"
echo "$resp" | grep -q 'Unknown variable: nope'
check "an undefined name is reported" $?
echo "$resp" | grep -q '"severity":1'
check "a type error is reported as an error, not a warning" $?
# The column is the whole point: an editor draws the squiggle here.
echo "$resp" | grep -q '"start":{"line":2,"character":13}'
check "the diagnostic points at the offending token (2:13)" $?
# Zero-width today (end == start). Pinned so the M5 widening is a visible change
# rather than a silent one; flip this to a token-width end when M5 lands.
echo "$resp" | grep -q '"end":{"line":2,"character":13}'
check "the diagnostic range is currently zero-width (end == start)" $?

# --- 2b. `no_prelude` scope: the editor must agree with the batch compiler --------
# Two front ends decide independently what is in scope for a `no_prelude` file, and for
# a while only one of them was fixed. `compiler.check_bundled` stopped seeding the
# checker with the prelude's schemes (`469924cf`, docs/no-prelude-core-v0.md) — that is
# what turned a call to a prelude function from a clang `use of undefined value` into a
# positioned diagnostic. The analysis service keeps a `load_prelude_pairs` call of its
# own, so "fixed in the batch compiler" carried no information about what an editor
# shows; it had to be driven to find out.
#
# It agrees, because that call is a cache pre-warm rather than an environment injection
# (`analysis_service_driver.build_startup_state` discards the pairs). Asserted here
# because nothing makes the two agree ON PURPOSE: a change to either side could split
# them, and the symptom would be an editor that accepts what the build rejects.
NP_URI="file:///tmp/lsp_smoke_no_prelude.sprout"

resp="$(drive "$INIT" "$(open_doc "$NP_URI" 'no_prelude\n\nfn main() -> Int !{IO} = range_count(range_up(1, 4))\n')")"
echo "$resp" | grep -q 'Unknown variable: range_count'
check "no_prelude: a prelude function is out of scope in the editor too" $?

# The control that pins the cause to the seeding. A name in NEITHER the prelude nor the
# bundle was rejected correctly throughout — so the assertion above is only evidence
# about prelude exports if this one holds too.
resp="$(drive "$INIT" "$(open_doc "$NP_URI" 'no_prelude\n\nfn main() -> Int !{IO} = totally_bogus_name(1)\n')")"
echo "$resp" | grep -q 'Unknown variable: totally_bogus_name'
check "no_prelude: a genuinely unknown name is rejected" $?

# The floor must survive. `no_prelude` drops the library, not the language: the
# prelude's primitive-only externs stay in scope (`bundler.prelude_floor`). Without
# this, both assertions above would also pass on a server that handed a `no_prelude`
# file nothing at all — which is the failure mode the floor exists to prevent, and the
# one a prototype shipped past a fully green suite.
resp="$(drive "$INIT" "$(open_doc "$NP_URI" 'no_prelude\n\nfn main() -> Int !{IO} = str_len(\"ab\")\n')")"
echo "$resp" | grep -q '"diagnostics":\[\]'
check "no_prelude: a floor name stays in scope" $?

# THE DISCRIMINATING ASSERTION. The floor is a CUT, and only a name on the far side of
# it shows where the cut is. `vector_length` is an extern like `str_len` and its
# signature mentions nothing the prelude declares (`Vector` is a builtin type constant),
# so a floor defined as "type-closed against the prelude" would admit it — the floor is
# deliberately narrower, scalars only. Without this, the assertion above would pass just
# as well on a server that handed a `no_prelude` file the whole prelude back.
resp="$(drive "$INIT" "$(open_doc "$NP_URI" 'no_prelude\n\nfn main() -> Int !{IO} = vector_length(vector_empty())\n')")"
echo "$resp" | grep -q 'Unknown variable: vector_length'
check "no_prelude: a non-floor extern is out of scope" $?

# And the whole section must be about `no_prelude`, not about the server being broken:
# with the directive removed, both names resolve.
resp="$(drive "$INIT" "$(open_doc "$NP_URI" 'module m\n\nfn main() -> Int !{IO} = str_len(\"ab\") + vector_length(vector_empty())\n')")"
echo "$resp" | grep -q '"diagnostics":\[\]'
check "no_prelude: dropping the directive restores both names" $?

# --- 3. document lifecycle -------------------------------------------------------
# didChange must re-check: opening broken and then fixing it must clear the squiggle.
resp="$(drive "$INIT" "$(open_doc "$URI" "$BAD")" "$(change_doc "$URI" "$GOOD")")"
[ "$(echo "$resp" | grep -c 'publishDiagnostics')" -eq 2 ] \
  && [ "$(echo "$resp" | grep -c '"diagnostics":\[\]')" -eq 1 ]
check "didChange re-checks the document and clears the diagnostic" $?

resp="$(drive "$INIT" "$(open_doc "$URI" "$BAD")" \
  '{"jsonrpc":"2.0","method":"textDocument/didClose","params":{"textDocument":{"uri":"'"$URI"'"}}}')"
[ "$(echo "$resp" | grep -c '"diagnostics":\[\]')" -eq 1 ]
check "didClose clears the document's diagnostics" $?

# --- 4. shutdown -----------------------------------------------------------------
resp="$(drive "$INIT" '{"jsonrpc":"2.0","id":9,"method":"shutdown","params":{}}' \
  '{"jsonrpc":"2.0","id":10,"method":"textDocument/hover","params":{}}')"
echo "$resp" | grep -q '"id":9,"result":null'
check "shutdown is acknowledged" $?
echo "$resp" | grep -q '"id":10' && echo "$resp" | grep -q '\-32600'
check "a request after shutdown is refused" $?

# --- 5. package roots ------------------------------------------------------------
# A dotted non-stdlib import resolves only when the server is given its root. Until this
# was wired, `--package-root` existed on the batch CLI alone, so an editor reported every
# name from such a module as unknown and underlined the whole project.
#
# Reuses the fixture the batch-CLI gate already has: same module, other front end.
PKG_ROOT="$ROOT/tests/conformance/package_resolution/roots"
PKG_URI="file:///tmp/lsp_smoke_pkg.sprout"
PKG_SRC='import demo.greet (greeting)\n\nlet x = greeting()\n'

resp="$(drive_with_roots "$PKG_ROOT" "$INIT" "$(open_doc "$PKG_URI" "$PKG_SRC")")"
echo "$resp" | grep -q '"diagnostics":\[\]'
check "a package-root import resolves when the root is registered" $?

# Negative control: without the root it must still fail. A pass here would mean the
# default search path had been widened, which is a different and worse bug.
resp="$(drive "$INIT" "$(open_doc "$PKG_URI" "$PKG_SRC")")"
echo "$resp" | grep -q 'Unknown variable: greeting'
check "the same import is unresolved with no root registered" $?

# --- 6. go to definition ---------------------------------------------------------
# Fixtures avoid `->` deliberately; `let` bindings exercise the same lookup.
DEF_URI="file:///tmp/lsp_smoke_def.sprout"
# Quotes inside the fixture must be escaped for the JSON string that carries it; an
# unescaped one makes didOpen unparseable, and the server then answers `null` to every
# definition request — which reads as "declined politely" rather than "never saw the file".
DEF_SRC='module app.d\nimport stdlib.string as string\n\nlet helper = 1\n\nlet y = helper\n\nlet z = string.trim(\" a \")\n'
def_req() { # id, line, character
  printf '{"jsonrpc":"2.0","id":%s,"method":"textDocument/definition","params":{"textDocument":{"uri":"%s"},"position":{"line":%s,"character":%s}}}' "$1" "$DEF_URI" "$2" "$3"
}

# Same file: `helper` on line 5 is declared on line 3 (0-based).
resp="$(drive "$INIT" "$(open_doc "$DEF_URI" "$DEF_SRC")" "$(def_req 21 5 9)")"
echo "$resp" | grep -q "\"id\":21,\"result\":{\"uri\":\"$DEF_URI\",\"range\":{\"start\":{\"line\":3,"
check "definition resolves a name declared in the same file" $?

# Across files: `string.trim` must land in stdlib/string.sprout, NOT in the open
# document. Getting the file right is the half that a same-file-only implementation
# would silently fail.
resp="$(drive "$INIT" "$(open_doc "$DEF_URI" "$DEF_SRC")" "$(def_req 22 7 16)")"
echo "$resp" | grep -q '"id":22,"result":{"uri":"file://.*/stdlib/string.sprout"'
check "definition follows a qualified name into the providing module" $?

# Declining is a correct answer. Locals and parameters have no recorded position, and
# guessing would send the cursor somewhere wrong — worse than saying nothing.
# Column 3 of `let helper = 1` is the space between the keyword and the name.
resp="$(drive "$INIT" "$(open_doc "$DEF_URI" "$DEF_SRC")" "$(def_req 23 3 3)")"
echo "$resp" | grep -q '"id":23,"result":null'
check "definition returns null rather than guessing on a non-name position" $?

# --- 7. hover --------------------------------------------------------------------
# Hover answers with the inferred type of the word under the cursor.
hov_req() { # id, uri, line, character
  printf '{"jsonrpc":"2.0","id":%s,"method":"textDocument/hover","params":{"textDocument":{"uri":"%s"},"position":{"line":%s,"character":%s}}}' "$1" "$2" "$3" "$4"
}

# `answer` on line 2 (0-based) of GOOD is bound to 42.
resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")" "$(hov_req 31 "$URI" 2 4)")"
echo "$resp" | grep -q '"id":31,"result":{"contents":{"kind":"plaintext","value":"Int"}}'
check "hover reports the inferred type of a local binding" $?

# A qualified name resolves through the import, so hover must render the imported
# scheme rather than failing on the dot.
resp="$(drive "$INIT" "$(open_doc "$DEF_URI" "$DEF_SRC")" "$(hov_req 32 "$DEF_URI" 7 16)")"
echo "$resp" | grep -q '"id":32,"result":{"contents":.*String'
check "hover reports the type of a qualified imported name" $?

# Declining is a correct answer, same as definition: no word under the cursor.
# This one PASSES VACUOUSLY while hover is unwired (everything answers null), so it
# proves nothing on its own — it earns its keep only once the assertions above are
# green, as the guard against answering a position that holds no name.
resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")" "$(hov_req 33 "$URI" 2 3)")"
echo "$resp" | grep -q '"id":33,"result":null'
check "hover returns null on a non-name position" $?

# THE DISCRIMINATING ASSERTION. Hover must honour `--package-root` exactly as
# diagnostics do. Routing hover through the `analysis_type_of_in_source` builtin
# cannot pass this: that builtin talks to a co-process started as
# `sproutd --analysis-service <stdlib_root>`, whose command line carries no package
# roots, so the import is unresolvable on the other side of the fork. An editor would
# then show correct diagnostics and a failing hover in the same file.
resp="$(drive_with_roots "$PKG_ROOT" "$INIT" "$(open_doc "$PKG_URI" "$PKG_SRC")" "$(hov_req 34 "$PKG_URI" 2 8)")"
echo "$resp" | grep -q '"id":34,"result":{"contents":'
check "hover resolves a name from a package-root module" $?

# A declared effect must reach the editor. A zero-parameter function has no arrow to
# carry one, so its effect lives only on the Scheme — and typing a *reference* to it
# loses that, which reported every `main` in the tree as pure `Unit`. Asserted here
# rather than only in the unit suite because hover is where a user reads it.
EFF='module main\n\nfn boot() -> Unit !{IO} = ()\n'
resp="$(drive "$INIT" "$(open_doc "$URI" "$EFF")" "$(hov_req 35 "$URI" 2 4)")"
echo "$resp" | grep -q '"id":35,"result":{"contents":{"kind":"plaintext","value":"Unit !{IO}"}}'
check "hover reports the effect of a nullary effectful function" $?

# The control. An effect that rides on an arrow always survived; asserting only the
# case above would not distinguish the fix from "print !{IO} everywhere".
resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")" "$(hov_req 36 "$URI" 2 4)")"
echo "$resp" | grep -q '"id":36,"result":{"contents":{"kind":"plaintext","value":"Int"}}'
check "hover reports no effect for a pure binding" $?

# --- 7b. the server announces itself ----------------------------------------------
# A `window/logMessage` on initialize is what puts the server in the IDE's own log. It
# exists because a real "go to definition does not work" report could not be diagnosed
# from the IDE at all — answering "is the server even running?" needed a process listing.
resp="$(drive "$INIT")"
echo "$resp" | grep -q '"method":"window/logMessage"'
check "the server announces itself on initialize" $?
# It must report the roots it was ACTUALLY given: a server running against the wrong
# stdlib or with no package roots looks identical from outside to a correct one.
resp="$(drive_with_roots "$PKG_ROOT" "$INIT")"
echo "$resp" | grep -q "package roots: $PKG_ROOT"
check "the announcement reports the package roots in effect" $?

# --- 8. unimplemented methods still answer ---------------------------------------
# JSON-RPC 2.0: every REQUEST must get a response. Only notifications may be dropped.
#
# This was found by driving the server with a conversation shaped like a real IDE's
# rather than the minimal one above: RubyMine sends documentSymbol, semanticTokens,
# codeAction and foldingRange on the first file it opens, and every one of them
# vanished without a reply, leaving the client waiting for a response that never came.
# The minimal smoke fixture never sent them, so nothing here noticed.
unknown_req='{"jsonrpc":"2.0","id":41,"method":"textDocument/documentSymbol","params":{"textDocument":{"uri":"'"$URI"'"}}}'
resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")" "$unknown_req")"
echo "$resp" | grep -q '"id":41' && echo "$resp" | grep -q '\-32601'
check "an unimplemented request is answered with method-not-found" $?

# A notification has no id and MUST NOT be answered — replying to one is as wrong as
# dropping a request. `$/cancelRequest` is the one a client sends constantly.
resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")" \
  '{"jsonrpc":"2.0","method":"$/cancelRequest","params":{"id":41}}')"
! echo "$resp" | grep -q '\-32601'
check "an unimplemented notification is dropped silently" $?

# The server must still be alive afterwards: an unknown method is not a fatal one.
resp="$(drive "$INIT" "$(open_doc "$DEF_URI" "$DEF_SRC")" "$unknown_req" "$(def_req 42 5 9)")"
echo "$resp" | grep -q '"id":42,"result":{"uri"'
check "the server keeps serving after an unimplemented request" $?

# --- 9. capability honesty -------------------------------------------------------
# Everything advertised must answer. This is the check that would have caught
# `hoverProvider: true` shipping alongside a handler that returns null unconditionally.
caps="$(drive "$INIT")"
hover_req='{"jsonrpc":"2.0","id":3,"method":"textDocument/hover","params":{"textDocument":{"uri":"'"$URI"'"},"position":{"line":2,"character":4}}}'
comp_req='{"jsonrpc":"2.0","id":4,"method":"textDocument/completion","params":{"textDocument":{"uri":"'"$URI"'"},"position":{"line":2,"character":4}}}'

if echo "$caps" | grep -q '"hoverProvider":true'; then
  resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")" "$hover_req")"
  ! echo "$resp" | grep -q '"id":3,"result":null'
  check "hoverProvider is advertised, so hover must answer" $?
else
  echo "SKIP hover is not advertised (honest: the handler is not wired yet)"
fi

if echo "$caps" | grep -q '"definitionProvider":true'; then
  resp="$(drive "$INIT" "$(open_doc "$DEF_URI" "$DEF_SRC")" "$(def_req 24 5 9)")"
  ! echo "$resp" | grep -q '"id":24,"result":null'
  check "definitionProvider is advertised, so definition must answer" $?
else
  echo "SKIP definition is not advertised"
fi

if echo "$caps" | grep -q '"completionProvider"'; then
  resp="$(drive "$INIT" "$(open_doc "$URI" "$GOOD")" "$comp_req")"
  ! echo "$resp" | grep -qE '"id":4,"result":(\[\]|null)'
  check "completionProvider is advertised, so completion must answer" $?
else
  echo "SKIP completion is not advertised (honest: the handler is not wired yet)"
fi

if [ "$fail" -eq 0 ]; then echo "==> lsp smoke: OK"; else echo "==> lsp smoke: FAILED"; fi
exit $fail
