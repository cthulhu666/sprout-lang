#!/usr/bin/env bash
# Generates the linear misuse grid (docs/linearity-bound-retro-2026-10-20.md §5 T2).
# Usage: scripts/gen_linear_grid.sh <out-root>
#   reject cells: <out-root>/tests/conformance/linear_grid/<path>__<misuse>.{spr,err}
#   accept cells: <out-root>/tests/conformance/run/linear_grid_<path>__<case>.{spr,out}
#   parse-time rejections: <out-root>/tests/conformance/parse_error/linear_grid_decl__<name>.{spr,err}
# A .err is a substring the `--phase check` output must contain.
#
# Rows (reach paths): direct, body, annsib, unannsib, inst, instb, method, mbound, ext,
# ctor, exist, tlet. Columns: copy, drop, step (discarded do-step), wild (`_ <-`), pass (to
# an unbounded generic), mode (a borrowing function where a consuming one is expected,
# §6.6). `_nolin` rows declare no linear type anywhere in the program. A `mode` cell marks
# the line its error is reported on with `#~^ ERROR` (scripts/error_line_marker.sh).
# `inst` also holds one cell per way a dictionary reaches an instance: nested context,
# superclass, tuple head, method value, an enclosing bound, an instance method's bound.
#
# N/A cells (not generated):
#   - direct/inst/method/ext/ctor/exist/tlet _nolin: they need a linear value to reach
#     the generic; with no linear type there is none.
#   - exist pack without the bound: type_error/linear_bound_existential_pack, which carries
#     the hint's fixed file.
#   - ctor pass of a bound variable: a data parameter takes no bound (§6.1).
set -euo pipefail

OUT="${1:?usage: gen_linear_grid.sh <out-root>}"
R="$OUT/tests/conformance/linear_grid"
A="$OUT/tests/conformance/run"
P="$OUT/tests/conformance/parse_error"
rm -rf "$R"; rm -f "$A"/linear_grid_* "$P"/linear_grid_*
mkdir -p "$R" "$A" "$P"

HDR_L='module main

type linear L = L Int

fn close(l: L) -> Int = match l with | L n -> n

fn dup(z: b) -> (b, b) = (z, z)
'
HDR_N='module main

fn dup(z: b) -> (b, b) = (z, z)
'
HDR_B='module main
'

# emit <dir> <prefix> <ext> <hdr> <name> <expected>  (program body on stdin)
emit() {
  { printf '%s\n' "$4"; cat; } > "$1/$2$5.spr"
  printf '%s\n' "$6" > "$1/$2$5.$3"
}
# rej <hdr> <name> <err>: a reject cell.
rej() { emit "$R" "" err "$@"; }
# rejp <hdr> <name> <err>: rejected at parse time, so it lives in parse_error/, which
# the pre-commit lint skips (a file that must not parse cannot be linted).
rejp() { emit "$P" linear_grid_ err "$@"; }
# acc <hdr> <name> <stdout>: an accept cell.
acc() { emit "$A" linear_grid_ out "$@"; }
# bound_msg <callee var> <bounded fn>: the obligation error's key phrase.
bound_msg() {
  printf 'takes `%s` as a value it may copy or drop, and `%s`'"'"'s `a` is linear under its `where linear a`' "$1" "$2"
}

TAKES='takes `a` as a value it may copy or drop'
# inst_msg <class> <head> <var> <reason>: the dictionary walk's error (§6.7).
inst_msg() {
  printf 'the `%s` instance for `%s` takes `%s` as a value it may copy or drop, and %s; if the instance uses its `%s` exactly once, add `where linear %s` to its head' "$1" "$2" "$3" "$4" "$3" "$3"
}
LIN='`L` is linear'

# ── direct: unbounded generic called at a linear type ─────────────────────────
rej "$HDR_L" direct__copy "$TAKES" <<'EOF'
fn g(x: a) -> (a, a) = (x, x)

fn main() -> Unit !{IO} = match g(L(1)) with | (p, q) -> print(int_to_string(close(p) + close(q)))
EOF
rej "$HDR_L" direct__drop "$TAKES" <<'EOF'
fn g(x: a, y: Int) -> Int = y

fn main() -> Unit !{IO} = print(int_to_string(g(L(1), 2)))
EOF
rej "$HDR_L" direct__step "$TAKES" <<'EOF'
fn g(x: a) -> Int =
  do
    x
    1

fn main() -> Unit !{IO} = print(int_to_string(g(L(1))))
EOF
rej "$HDR_L" direct__wild "$TAKES" <<'EOF'
fn g(x: a) -> Int !{IO} =
  do
    _ <- after(print("w"), x)
    1

fn main() -> Unit !{IO} = print(int_to_string(g(L(1))))
EOF
rej "$HDR_L" direct__pass "$TAKES" <<'EOF'
fn g(x: a) -> (a, a) = dup(x)

fn main() -> Unit !{IO} = match g(L(1)) with | (p, q) -> print(int_to_string(close(p) + close(q)))
EOF

# ── body: a declaration's own `where linear a` ────────────────────────────────
body_cells() { # suffix hdr value show
  local s="$1" h="$2" v="$3" show="$4"
  rej "$h" "body${s}__copy" "is used more than once" <<EOF
fn g(x: a) -> (a, a) where linear a = (x, x)

fn main() -> Unit !{IO} = match g($v) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
  rej "$h" "body${s}__drop" "is never used" <<EOF
fn g(x: a, y: Int) -> Int where linear a = y

fn main() -> Unit !{IO} = print(int_to_string(g($v, 2)))
EOF
  rej "$h" "body${s}__step" "is referenced as a statement" <<EOF
fn g(x: a) -> Int where linear a =
  do
    x
    1

fn main() -> Unit !{IO} = print(int_to_string(g($v)))
EOF
  rej "$h" "body${s}__wild" "discards a linear value" <<EOF
fn g(x: a) -> Int !{IO} where linear a =
  do
    _ <- after(print("w"), x)
    1

fn main() -> Unit !{IO} = print(int_to_string(g($v)))
EOF
  rej "$h" "body${s}__pass" "$(bound_msg b g)" <<EOF
fn g(x: a) -> (a, a) where linear a = dup(x)

fn main() -> Unit !{IO} = match g($v) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
}
body_cells "" "$HDR_L" 'L(1)' close
body_cells _nolin "$HDR_N" 1 ''

# ── annsib: annotated binding-group sibling, no bound of its own ──────────────
annsib_cells() { # suffix hdr value show
  local s="$1" h="$2" v="$3" show="$4"
  local E="$(bound_msg a ping)"
  rej "$h" "annsib${s}__copy" "$E" <<EOF
fn ping(x: a, n: Int) -> (a, a) where linear a = pong(x, n)

fn pong(y: a, n: Int) -> (a, a) = if n > 0 then ping(y, n - 1) else (y, y)

fn main() -> Unit !{IO} = match ping($v, 2) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
  rej "$h" "annsib${s}__drop" "$E" <<EOF
fn ping(x: a, n: Int) -> Int where linear a = pong(x, n)

fn pong(y: a, n: Int) -> Int = if n > 0 then ping(y, n - 1) else 1

fn main() -> Unit !{IO} = print(int_to_string(ping($v, 2)))
EOF
  rej "$h" "annsib${s}__step" "$E" <<EOF
fn stmt(y: a) -> Int =
  do
    y
    1

fn ping(x: a, n: Int) -> Int where linear a = pong(x, n)

fn pong(y: a, n: Int) -> Int = if n > 0 then ping(y, n - 1) else stmt(y)

fn main() -> Unit !{IO} = print(int_to_string(ping($v, 2)))
EOF
  rej "$h" "annsib${s}__wild" "$E" <<EOF
fn ping(x: a, n: Int) -> Int !{IO} where linear a = pong(x, n)

fn pong(y: a, n: Int) -> Int !{IO} =
  if n > 0 then ping(y, n - 1)
  else
    do
      _ <- after(print("w"), y)
      1

fn main() -> Unit !{IO} = print(int_to_string(ping($v, 2)))
EOF
  rej "$h" "annsib${s}__pass" "$E" <<EOF
fn ping(x: a, n: Int) -> (a, a) where linear a = pong(x, n)

fn pong(y: a, n: Int) -> (a, a) = if n > 0 then ping(y, n - 1) else dup(y)

fn main() -> Unit !{IO} = match ping($v, 2) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
}
annsib_cells "" "$HDR_L" 'L(1)' close
annsib_cells _nolin "$HDR_N" 1 ''

# ── unannsib: unannotated sibling handed the bounded variable ─────────────────
unannsib_cells() { # suffix hdr value show
  local s="$1" h="$2" v="$3" show="$4"
  rej "$h" "unannsib${s}__copy" "is used more than once" <<EOF
fn ping(x: a, n: Int) -> (a, a) where linear a = pong(x, n)

fn pong(y, n: Int) = if n > 0 then ping(y, n - 1) else (y, y)

fn main() -> Unit !{IO} = match ping($v, 2) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
  rej "$h" "unannsib${s}__drop" "is used in some branches but not others" <<EOF
fn ping(x: a, n: Int) -> Int where linear a = pong(x, n)

fn pong(y, n: Int) = if n > 0 then ping(y, n - 1) else 1

fn main() -> Unit !{IO} = print(int_to_string(ping($v, 2)))
EOF
  rej "$h" "unannsib${s}__step" "is referenced as a statement" <<EOF
fn ping(x: a, n: Int) -> Int where linear a = pong(x, n)

fn pong(y, n: Int) =
  if n > 0 then ping(y, n - 1)
  else
    do
      y
      1

fn main() -> Unit !{IO} = print(int_to_string(ping($v, 2)))
EOF
  rej "$h" "unannsib${s}__wild" '`_ <-` discards a linear value' <<EOF
fn ping(x: a, n: Int) -> Int !{IO} where linear a = pong(x, n)

fn pong(y, n: Int) =
  if n > 0 then ping(y, n - 1)
  else
    do
      _ <- after(print("w"), y)
      1

fn main() -> Unit !{IO} = print(int_to_string(ping($v, 2)))
EOF
  rej "$h" "unannsib${s}__pass" "$(bound_msg b ping)" <<EOF
fn ping(x: a, n: Int) -> (a, a) where linear a = pong(x, n)

fn pong(y, n: Int) = if n > 0 then ping(y, n - 1) else dup(y)

fn main() -> Unit !{IO} = match ping($v, 2) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
}
unannsib_cells "" "$HDR_L" 'L(1)' close
unannsib_cells _nolin "$HDR_N" 1 ''

# ── inst: an instance head's variable, reached through a dictionary (§6.7) ─────
rej "$HDR_L" inst__copy "$(inst_msg Dup2 'Maybe a' a "$LIN")" <<'EOF'
class Dup2 t
  fn dup2(x: t) -> (t, t)

instance Dup2 (Maybe a)
  fn dup2(x: Maybe a) -> (Maybe a, Maybe a) = (x, x)

fn eat(m: Maybe L) -> Int = match m with | Just l -> close(l) | Nothing -> 0

fn main() -> Unit !{IO} = match dup2(Just (L(1))) with | (p, q) -> print(int_to_string(eat(p) + eat(q)))
EOF
rej "$HDR_L" inst__drop "$(inst_msg Drop2 'Maybe a' a "$LIN")" <<'EOF'
class Drop2 t
  fn drop2(x: t) -> Int

instance Drop2 (Maybe a)
  fn drop2(x: Maybe a) -> Int = 1

fn main() -> Unit !{IO} = print(int_to_string(drop2(Just (L(1)))))
EOF
rej "$HDR_L" inst__step "$(inst_msg Count 'Maybe a' a "$LIN")" <<'EOF'
class Count t
  fn count(x: t) -> Int

fn first(m: Maybe a) -> a = match m with | Just v -> v | Nothing -> panic("none")

instance Count (Maybe a)
  fn count(x: Maybe a) -> Int =
    do
      first(x)
      0

fn main() -> Unit !{IO} = print(int_to_string(count(Just (L(1)))))
EOF
rej "$HDR_L" inst__wild "$(inst_msg Count 'Maybe a' a "$LIN")" <<'EOF'
class Count t
  fn count(x: t) -> Maybe Int

instance Count (Maybe a)
  fn count(x: Maybe a) -> Maybe Int =
    do
      _ <- x
      Just(0)

fn main() -> Unit !{IO} =
  print(match count(Just (L(1))) with | Just n -> int_to_string(n) | Nothing -> "none")
EOF
rej "$HDR_L" inst__pass "$(inst_msg Dup2 'Maybe a' a "$LIN")" <<'EOF'
class Dup2 t
  fn dup2(x: t) -> (t, t)

instance Dup2 (Maybe a)
  fn dup2(x: Maybe a) -> (Maybe a, Maybe a) = dup(x)

fn eat(m: Maybe L) -> Int = match m with | Just l -> close(l) | Nothing -> 0

fn main() -> Unit !{IO} = match dup2(Just (L(1))) with | (p, q) -> print(int_to_string(eat(p) + eat(q)))
EOF

TAKER='class Taker t
  fn take(x: t) -> Int

instance Taker L
  fn take(x: L) -> Int = close(x)

type Box a = | Box a

type Wrap a = | Wrap a
'
# A context dictionary: `Box` is bounded, the `Wrap` inside it is not.
rej "$HDR_L" inst__nested_ctx "$(inst_msg Taker 'Wrap a' a "$LIN")" <<EOF
$TAKER
instance Taker (Box a) where linear a, Taker a
  fn take(x: Box a) -> Int = match x with | Box y -> take(y)

instance Taker (Wrap a) where Taker a
  fn take(x: Wrap a) -> Int = match x with | Wrap y -> take(y)

fn main() -> Unit !{IO} = print(int_to_string(take(Box (Wrap (L(1))))))
EOF
# A superclass dictionary, packed beside the one the call names.
rej "$HDR_L" inst__super "$(inst_msg Base 'Maybe a' a "$LIN")" <<'EOF'
class Base t
  fn base(x: t) -> Int

class Sub t where Base t
  fn sub(x: t) -> t

instance Base (Maybe a)
  fn base(x: Maybe a) -> Int = 0

instance Sub (Maybe a) where linear a
  fn sub(x: Maybe a) -> Maybe a = x

fn main() -> Unit !{IO} = print(int_to_string(match sub(Just (L(1))) with | Just l -> close(l) | Nothing -> 0))
EOF
rej "$HDR_L" inst__tuple "$(inst_msg Taker '(a, b)' a "$LIN")" <<EOF
$TAKER
instance Taker (a, b)
  fn take(x: (a, b)) -> Int = 0

fn main() -> Unit !{IO} = print(int_to_string(take((L(1), 2))))
EOF
# A method passed as a value: its dictionary is built where it is named.
rej "$HDR_L" inst__method_ref "$(inst_msg Taker 'Box a' a "$LIN")" <<EOF
$TAKER
instance Taker (Box a) where Taker a
  fn take(x: Box a) -> Int = match x with | Box y -> take(y) + take(y)

fn apply(f: Box L -> Int, b: Box L) -> Int = f(b)

fn main() -> Unit !{IO} = print(int_to_string(apply(take, Box (L(1)))))
EOF
# The enclosing declaration's own bound makes its `a` linear at the dictionary.
rej "$HDR_L" inst__fwd_bounded "$(inst_msg Taker 'Box a' a '`wrapit`'"'"'s `a` is linear under its `where linear a`')" <<EOF
$TAKER
instance Taker (Box a) where Taker a
  fn take(x: Box a) -> Int = match x with | Box y -> take(y) + take(y)

fn wrapit(x: a) -> Int where linear a, Taker a = take(Box(x))

fn main() -> Unit !{IO} = print(int_to_string(wrapit(L(1))))
EOF
# The instance head's own bound makes its `a` linear at a dictionary in its body.
rej "$HDR_L" inst__head_bounded "$(inst_msg Taker 'Wrap a' a '`Taker (Box a)`'"'"'s `a` is linear under its `where linear a`')" <<EOF
$TAKER
instance Taker (Wrap a) where Taker a
  fn take(x: Wrap a) -> Int = match x with | Wrap y -> take(y) + take(y)

instance Taker (Box a) where linear a, Taker a
  fn take(x: Box a) -> Int = match x with | Box y -> take(Wrap(y))

fn main() -> Unit !{IO} = print(int_to_string(take(Box (L(1)))))
EOF
# An instance method's own bound, spelled as the instance writes it.
rej "$HDR_L" inst__method_bound "$(inst_msg Taker 'Box a' a '`m`'"'"'s `b` is linear under its `where linear b`')" <<EOF
$TAKER
instance Taker (Box a) where Taker a
  fn take(x: Box a) -> Int = match x with | Box y -> take(y) + take(y)

class K t
  fn m(s: t, x: c) -> Int where linear c, Taker c

instance K Int
  fn m(s: Int, x: b) -> Int where linear b, Taker b = take(Box(x))

fn main() -> Unit !{IO} = print(int_to_string(m(0, L(1))))
EOF
# Prelude instances that cannot take the bound: each drops a value on some path.
rej "$HDR_L" inst__prelude_eq_maybe "$(inst_msg Eq 'Maybe a' a "$LIN")" <<'EOF'
instance Eq L
  fn eq(x: L, y: L) -> Bool = close(x) == close(y)

fn main() -> Unit !{IO} = print(if Just(L(1)) == Just (L(2)) then "eq" else "ne")
EOF
rej "$HDR_L" inst__prelude_list_eq "$(inst_msg Eq 'List a' a "$LIN")" <<'EOF'
instance Eq L
  fn eq(x: L, y: L) -> Bool = close(x) == close(y)

fn main() -> Unit !{IO} = print(if [L(1)] == [L(2)] then "eq" else "ne")
EOF

# ── instb: an instance head's own `where linear a` ────────────────────────────
instb_cells() { # suffix hdr value type eat-body
  local s="$1" h="$2" v="$3" ty="$4" eat="$5" jv
  # `Just` applied as fmt writes it: spaced before a call, not before a literal.
  if [[ "$v" == *"("* ]]; then jv="Just ($v)"; else jv="Just($v)"; fi
  rej "$h" "instb${s}__copy" "is used more than once" <<EOF
class Dup2 t
  fn dup2(x: t) -> (t, t)

instance Dup2 (Maybe a) where linear a
  fn dup2(x: Maybe a) -> (Maybe a, Maybe a) = (x, x)

fn eat(m: Maybe $ty) -> Int = $eat

fn main() -> Unit !{IO} = match dup2($jv) with | (p, q) -> print(int_to_string(eat(p) + eat(q)))
EOF
  rej "$h" "instb${s}__drop" "is never used" <<EOF
class Drop2 t
  fn drop2(x: t, n: Int) -> Int

instance Drop2 (Maybe a) where linear a
  fn drop2(x: Maybe a, n: Int) -> Int = n

fn main() -> Unit !{IO} = print(int_to_string(drop2($jv, 1)))
EOF
  rej "$h" "instb${s}__step" "is referenced as a statement" <<EOF
class Count t
  fn count(x: t) -> Int

instance Count (Maybe a) where linear a
  fn count(x: Maybe a) -> Int =
    do
      x
      0

fn main() -> Unit !{IO} = print(int_to_string(count($jv)))
EOF
  rej "$h" "instb${s}__wild" "discards a linear value" <<EOF
class Count t
  fn count(x: t) -> Int !{IO}

instance Count (Maybe a) where linear a
  fn count(x: Maybe a) -> Int !{IO} =
    match x with
    | Just y ->
      do
        _ <- after(print("w"), y)
        0
    | Nothing -> 0

fn main() -> Unit !{IO} = print(int_to_string(count($jv)))
EOF
  rej "$h" "instb${s}__pass" "$(bound_msg b 'Dup2 (Maybe a)')" <<EOF
class Dup2 t
  fn dup2(x: t) -> (t, t)

instance Dup2 (Maybe a) where linear a
  fn dup2(x: Maybe a) -> (Maybe a, Maybe a) = dup(x)

fn eat(m: Maybe $ty) -> Int = $eat

fn main() -> Unit !{IO} = match dup2($jv) with | (p, q) -> print(int_to_string(eat(p) + eat(q)))
EOF
}
instb_cells "" "$HDR_L" 'L(1)' L 'match m with | Just l -> close(l) | Nothing -> 0'
instb_cells _nolin "$HDR_N" 1 Int 'maybe_with_default(0, m)'

# ── method: a class method's own variable, unbounded ──────────────────────────
rej "$HDR_L" method__copy "$TAKES" <<'EOF'
class K t
  fn m(s: t, x: a) -> (a, a)

instance K Int
  fn m(s: Int, x: a) -> (a, a) = (x, x)

fn main() -> Unit !{IO} = match m(0, L(1)) with | (p, q) -> print(int_to_string(close(p) + close(q)))
EOF
rej "$HDR_L" method__drop "$TAKES" <<'EOF'
class K t
  fn m(s: t, x: a) -> Int

instance K Int
  fn m(s: Int, x: a) -> Int = s

fn main() -> Unit !{IO} = print(int_to_string(m(0, L(1))))
EOF
rej "$HDR_L" method__step "$TAKES" <<'EOF'
class K t
  fn m(s: t, x: a) -> Int

instance K Int
  fn m(s: Int, x: a) -> Int =
    do
      x
      s

fn main() -> Unit !{IO} = print(int_to_string(m(0, L(1))))
EOF
rej "$HDR_L" method__wild "$TAKES" <<'EOF'
class K t
  fn m(s: t, x: a) -> Int !{IO}

instance K Int
  fn m(s: Int, x: a) -> Int !{IO} =
    do
      _ <- after(print("w"), x)
      s

fn main() -> Unit !{IO} = print(int_to_string(m(0, L(1))))
EOF
rej "$HDR_L" method__pass "$TAKES" <<'EOF'
class K t
  fn m(s: t, x: a) -> (a, a)

instance K Int
  fn m(s: Int, x: a) -> (a, a) = dup(x)

fn main() -> Unit !{IO} = match m(0, L(1)) with | (p, q) -> print(int_to_string(close(p) + close(q)))
EOF

# ── mbound: a class method's own variable with `where linear a` ───────────────
mbound_cells() { # suffix hdr value show
  local s="$1" h="$2" v="$3" show="$4"
  rej "$h" "mbound${s}__copy" "is used more than once" <<EOF
class K t
  fn m(s: t, x: a) -> (a, a) where linear a

instance K Int
  fn m(s: Int, x: a) -> (a, a) where linear a = (x, x)

fn main() -> Unit !{IO} = match m(0, $v) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
  rej "$h" "mbound${s}__drop" "is never used" <<EOF
class K t
  fn m(s: t, x: a) -> Int where linear a

instance K Int
  fn m(s: Int, x: a) -> Int where linear a = s

fn main() -> Unit !{IO} = print(int_to_string(m(0, $v)))
EOF
  rej "$h" "mbound${s}__step" "is referenced as a statement" <<EOF
class K t
  fn m(s: t, x: a) -> Int where linear a

instance K Int
  fn m(s: Int, x: a) -> Int where linear a =
    do
      x
      s

fn main() -> Unit !{IO} = print(int_to_string(m(0, $v)))
EOF
  rej "$h" "mbound${s}__wild" "discards a linear value" <<EOF
class K t
  fn m(s: t, x: a) -> Int !{IO} where linear a

instance K Int
  fn m(s: Int, x: a) -> Int !{IO} where linear a =
    do
      _ <- after(print("w"), x)
      s

fn main() -> Unit !{IO} = print(int_to_string(m(0, $v)))
EOF
  rej "$h" "mbound${s}__pass" "$(bound_msg b m)" <<EOF
class K t
  fn m(s: t, x: a) -> (a, a) where linear a

instance K Int
  fn m(s: Int, x: a) -> (a, a) where linear a = dup(x)

fn main() -> Unit !{IO} = match m(0, $v) with | (p, q) -> print(int_to_string($show(p) + $show(q)))
EOF
}
mbound_cells "" "$HDR_L" 'L(1)' close
mbound_cells _nolin "$HDR_N" 1 ''

# ── ext: an extern that takes the variable without a bound ────────────────────
rej "$HDR_L" ext__copy "$TAKES" <<'EOF'
extern fn ext_dup(x: a) -> (a, a)

fn main() -> Unit !{IO} = match ext_dup(L(1)) with | (p, q) -> print(int_to_string(close(p) + close(q)))
EOF
rej "$HDR_L" ext__drop "$TAKES" <<'EOF'
extern fn ext_drop(x: a) -> Int

fn main() -> Unit !{IO} = print(int_to_string(ext_drop(L(1))))
EOF
rej "$HDR_L" ext__step "this step discards a linear value" <<'EOF'
extern fn ext_id(x: a) -> a

fn main() -> Unit !{IO} =
  do
    ext_id(L(1))
    print("x")
EOF
rej "$HDR_L" ext__wild '`_ <-` discards a linear value' <<'EOF'
extern fn ext_io(x: a) -> a !{IO}

fn main() -> Unit !{IO} =
  do
    _ <- ext_io(L(1))
    print("x")
EOF
rej "$HDR_L" ext__pass "$(bound_msg a g)" <<'EOF'
extern fn ext_id(x: a) -> a

fn g(x: a) -> a where linear a = ext_id(x)

fn main() -> Unit !{IO} = print(int_to_string(close(g(L(1)))))
EOF

# ── ctor: a constructor stores its argument; containment tracks the holder ────
rej "$HDR_L" ctor__copy "is used more than once" <<'EOF'
type Box a = Box a

fn eat(b: Box L) -> Int = match b with | Box l -> close(l)

fn main() -> Unit !{IO} =
  do
    let b = Box(L(1))
    print(int_to_string(eat(b) + eat(b)))
EOF
rej "$HDR_L" ctor__drop "is never used" <<'EOF'
type Box a = Box a

fn main() -> Unit !{IO} =
  do
    let b = Box(L(1))
    print("x")
EOF
rej "$HDR_L" ctor__step "this step discards a linear value" <<'EOF'
type Box a = Box a

fn main() -> Unit !{IO} =
  do
    Box(L(1))
    print("x")
EOF
rej "$HDR_L" ctor__wild '`_ <-` discards a linear value' <<'EOF'
type Box a = Box a

fn main() -> Unit !{IO} =
  do
    _ <- after(print("w"), Box (L(1)))
    print("x")
EOF
rej "$HDR_L" ctor__pass "$TAKES" <<'EOF'
type Box a = Box a

fn ident(x: a) -> a = x

fn main() -> Unit !{IO} = match ident(Box (L(1))) with | Box l -> print(int_to_string(close(l)))
EOF

# ── exist: a constructor's `where linear a`, and what a match unpacks ─────────
# The bound makes `Boxed` linear whatever it carries; the unpacked `x` is linear too.
EXB='class Taker a
  fn take(x: a) -> Int

instance Taker L
  fn take(x: L) -> Int = close(x)

instance Taker Int
  fn take(x: Int) -> Int = x

type Boxed = | exists a. Boxed a where linear a, Taker a

fn unbox(b: Boxed) -> Int = match b with | Boxed x -> take(x)
'
UNPACKED='`Boxed`'"'"'s `a` is linear under its `where linear a`'
rej "$HDR_L" exist__copy "is used more than once" <<EOF
$EXB
fn main() -> Unit !{IO} =
  do
    let b = Boxed(L(1))
    print(int_to_string(unbox(b) + unbox(b)))
EOF
rej "$HDR_L" exist__drop "is never used" <<EOF
$EXB
fn main() -> Unit !{IO} =
  do
    let b = Boxed(L(1))
    print("x")
EOF
rej "$HDR_L" exist__step "is referenced as a statement" <<EOF
$EXB
fn g(b: Boxed) -> Int =
  do
    b
    1

fn main() -> Unit !{IO} = print(int_to_string(g(Boxed (L(1)))))
EOF
rej "$HDR_L" exist__wild "discards a linear value" <<EOF
$EXB
fn g(b: Boxed) -> Int !{IO} =
  do
    _ <- after(print("w"), b)
    1

fn main() -> Unit !{IO} = print(int_to_string(g(Boxed (L(1)))))
EOF
rej "$HDR_L" exist__pass 'takes `b` as a value it may copy or drop, and `main.Boxed` is linear' <<EOF
$EXB
fn main() -> Unit !{IO} = match dup(Boxed (L(1))) with | (p, q) -> print(int_to_string(unbox(p) + unbox(q)))
EOF
rej "$HDR_L" exist__nonlinear_copy "is used more than once" <<EOF
$EXB
fn main() -> Unit !{IO} =
  do
    let b = Boxed(1)
    print(int_to_string(unbox(b) + unbox(b)))
EOF
rej "$HDR_L" exist__unpack_copy "is used more than once" <<EOF
$EXB
fn twice(b: Boxed) -> Int = match b with | Boxed x -> take(x) + take(x)

fn main() -> Unit !{IO} = print(int_to_string(twice(Boxed (L(1)))))
EOF
rej "$HDR_L" exist__unpack_drop "is never used" <<EOF
$EXB
fn none(b: Boxed) -> Int = match b with | Boxed x -> 0

fn main() -> Unit !{IO} = print(int_to_string(none(Boxed (L(1)))))
EOF
rej "$HDR_L" exist__unpack_pass "takes \`b\` as a value it may copy or drop, and $UNPACKED" <<EOF
$EXB
fn pass(b: Boxed) -> Int = match b with | Boxed x -> match dup(x) with | (p, q) -> take(p) + take(q)

fn main() -> Unit !{IO} = print(int_to_string(pass(Boxed (L(1)))))
EOF
rej "$HDR_L" exist__unpack_dict "$(inst_msg Taker 'Box a' a "$UNPACKED")" <<EOF
$EXB
type Box a = | Box a

instance Taker (Box a)
  fn take(x: Box a) -> Int = 0

fn viabox(b: Boxed) -> Int = match b with | Boxed x -> take(Box(x))

fn main() -> Unit !{IO} = print(int_to_string(viabox(Boxed (L(1)))))
EOF
rej "$HDR_L" exist__let_unpack_copy "is used more than once" <<EOF
$EXB
let k: Int = match Boxed(L(1)) with | Boxed x -> take(x) + take(x)

fn main() -> Unit !{IO} = print(int_to_string(k))
EOF
rej "$HDR_L" exist__let_unpack_drop "is never used" <<EOF
$EXB
let k: Int = match Boxed(L(1)) with | Boxed x -> 0

fn main() -> Unit !{IO} = print(int_to_string(k))
EOF

# ── tlet: a top-level `let` is generalized and takes no bound ─────────────────
TLET='a top-level `let` takes no bound'
rej "$HDR_L" tlet__copy "$TLET" <<'EOF'
let dupl = \x -> (x, x)

fn main() -> Unit !{IO} = match dupl(L(1)) with | (p, q) -> print(int_to_string(close(p) + close(q)))
EOF
rej "$HDR_L" tlet__drop "$TLET" <<'EOF'
let konst1 = \x -> 1

fn main() -> Unit !{IO} = print(int_to_string(konst1(L(1))))
EOF
rej "$HDR_L" tlet__step "$TLET" <<'EOF'
let stmt1 = \x ->
  do
    x
    1

fn main() -> Unit !{IO} = print(int_to_string(stmt1(L(1))))
EOF
rej "$HDR_L" tlet__wild "$TLET" <<'EOF'
let wild1 = \x ->
  do
    _ <- after(print("w"), x)
    1

fn main() -> Unit !{IO} = print(int_to_string(wild1(L(1))))
EOF
rej "$HDR_L" tlet__pass "$TLET" <<'EOF'
let fwd = \x -> dup(x)

fn main() -> Unit !{IO} = match fwd(L(1)) with | (p, q) -> print(int_to_string(close(p) + close(q)))
EOF

# ── mode: a borrowing function where a consuming one is expected (§6.6) ─────────
# The pair is decided at the declaration that holds it, so each cell marks that line.
MODE='these differ only in how the parameter is taken, and a `borrowing` parameter'"'"'s convention must match exactly in either direction: a borrowing function standing in for a consuming one leaves the value unreleased, and a consuming one standing in for a borrowing one releases a value its caller still owns'
MODEL='fn peek(l: borrowing L) -> Int = match l with | L n -> n

fn run(f: L -> Int, x: L) -> Int = f(x)
'
MODEA='fn peek_a(r: borrowing a) -> Int where linear a = 1

fn run_a(f: a -> Int, x: a) -> Int where linear a = f(x)
'
rej "$HDR_L" direct__mode "$MODE" <<EOF
$MODEL
fn g(x: L) -> Int = run(peek, x)
#~^ ERROR

fn main() -> Unit !{IO} = print(int_to_string(g(L(1))))
EOF
rej "$HDR_L" body__mode "$MODE" <<EOF
$MODEA
fn g(x: a) -> Int where linear a = run_a(peek_a, x)
#~^ ERROR

fn main() -> Unit !{IO} = print(int_to_string(g(L(1))))
EOF
rej "$HDR_L" annsib__mode "$MODE" <<EOF
$MODEL
fn ping(x: L, n: Int) -> Int = if n > 0 then pong(x, n - 1) else close(x)

fn pong(y: L, n: Int) -> Int = if n > 0 then ping(y, n) else run(peek, y)
#~^ ERROR

fn main() -> Unit !{IO} = print(int_to_string(ping(L(1), 2)))
EOF
rej "$HDR_L" unannsib__mode "$MODE" <<EOF
$MODEL
fn ping(x: L, n: Int) -> Int = if n > 0 then pong(x, n - 1) else close(x)

fn pong(y, n: Int) = if n > 0 then ping(y, n) else run(peek, y)
#~^ ERROR

fn main() -> Unit !{IO} = print(int_to_string(ping(L(1), 2)))
EOF
rej "$HDR_L" inst__mode "$MODE" <<EOF
$MODEL
class Use a
  fn use(x: a) -> Int

instance Use L
  fn use(x: L) -> Int = run(peek, x)
  #~^ ERROR

fn main() -> Unit !{IO} = print(int_to_string(use(L(1))))
EOF
rej "$HDR_L" tlet__mode "$MODE" <<EOF
$MODEL
let k: Int = run(peek, L(1))
#~^ ERROR

fn main() -> Unit !{IO} = print(int_to_string(k))
EOF
rej "$HDR_L" exist__mode "$MODE" <<EOF
$MODEA
type Lent = | exists a. Lent a where linear a

fn g(b: Lent) -> Int = match b with | Lent x -> run_a(peek_a, x)
#~^ ERROR

fn main() -> Unit !{IO} = print(int_to_string(g(Lent (L(1)))))
EOF

# ── decl: misuses in declarations ─────────────────────────────────────────────
rejp "$HDR_B" decl__class_header_bound 'takes no `linear` bound' <<'EOF'
class C a where linear a
  fn c(x: a) -> Int

fn main() -> Unit !{IO} = print("x")
EOF
rej "$HDR_B" decl__method_bound_on_class_var 'names the class'"'"'s own type variable' <<'EOF'
class Taker a
  fn take(x: a) -> Int where linear a

instance Taker Int
  fn take(x: Int) -> Int = x + x

fn main() -> Unit !{IO} = print(int_to_string(take(2)))
EOF
rejp "$HDR_B" decl__extern_class_constraint 'takes only `where linear` bounds' <<'EOF'
extern fn ext_show(x: a) -> String where ToString a

fn main() -> Unit !{IO} = print("x")
EOF
rej "$HDR_B" decl__extern_redecl_drops_bound 'is declared more than once with different `where linear` bounds' <<'EOF'
extern fn give_up(msg: String) -> a where linear a
extern fn give_up(msg: String) -> a

fn main() -> Unit !{IO} = print("x")
EOF
rej "$HDR_B" decl__extern_redecl_adds_bound 'is declared more than once with different `where linear` bounds' <<'EOF'
extern fn give_up(msg: String) -> a
extern fn give_up(msg: String) -> a where linear a

fn main() -> Unit !{IO} = print("x")
EOF
rejp "$HDR_B" decl__bound_on_concrete_type '`where linear` takes a type variable' <<'EOF'
fn f(x: Int) -> Int where linear Int = x

fn main() -> Unit !{IO} = print(int_to_string(f(1)))
EOF
rejp "$HDR_B" decl__bound_on_applied_var '`where linear` takes a type variable' <<'EOF'
fn f(x: g a) -> g a where linear g a = x

fn main() -> Unit !{IO} = print("x")
EOF
rejp "$HDR_B" decl__bound_bare '`where linear` takes a type variable' <<'EOF'
fn f(x: a) -> a where linear = x

fn main() -> Unit !{IO} = print("x")
EOF
rej "$HDR_B" decl__bound_var_not_in_signature 'which does not appear in the signature of `ident`' <<'EOF'
fn ident(x: a) -> a where linear b = x

fn main() -> Unit !{IO} = print(int_to_string(ident(1)))
EOF
rej "$HDR_B" decl__extern_borrowing_unbounded 'needs `where linear` on it' <<'EOF'
extern fn peek_any(x: borrowing a) -> Int

fn main() -> Unit !{IO} = print("x")
EOF
rej "$HDR_B" decl__fn_borrowing_unbounded 'needs `where linear` on it' <<'EOF'
fn peek_any(x: borrowing a) -> Int = 1

fn main() -> Unit !{IO} = print(int_to_string(peek_any(1)))
EOF
rej "$HDR_B" decl__instance_method_omits_bound 'instance method `wrap1` omits `where linear a`' <<'EOF'
class Wrap f
  fn wrap1(x: a) -> f a where linear a

type One a = One a

instance Wrap One
  fn wrap1(x: a) -> One a = One(x)

fn main() -> Unit !{IO} = print("x")
EOF
rej "$HDR_B" decl__instance_method_adds_bound 'adds `where linear a`, which the class declaration does not declare' <<'EOF'
class Wrap f
  fn wrap1(x: a) -> f a

type One a = One a

instance Wrap One
  fn wrap1(x: a) -> One a where linear a = One(x)

fn main() -> Unit !{IO} = print("x")
EOF
rej "$HDR_B" decl__instance_bound_not_in_head '`where linear b` names `b`, which does not appear in the instance head `Taker (Box a)`; a bound lets a variable of the head be linear' <<'EOF'
class Taker a
  fn take(x: a) -> Int

type Box a = | Box a

instance Taker (Box a) where linear b
  fn take(x: Box a) -> Int = 0

fn main() -> Unit !{IO} = print("x")
EOF

# ── accept cells ──────────────────────────────────────────────────────────────
acc "$HDR_L" annsib__mode_nonlinear '1' <<EOF
$MODEA
fn run_i(f: Int -> Int, x: Int) -> Int = f(x)

fn ping(n: Int) -> Int = if n > 0 then pong(n - 1) else 0

fn pong(n: Int) -> Int = if n > 0 then ping(n - 1) else run_i(peek_a, n)

fn main() -> Unit !{IO} = print(int_to_string(ping(3)))
EOF
acc "$HDR_L" direct__bounded_generic $'3\n2' <<'EOF'
fn ident(x: a) -> a where linear a = x

fn main() -> Unit !{IO} =
  do
    print(int_to_string(close(ident(L(3)))))
    print(int_to_string(ident(2)))
EOF
acc "$HDR_L" direct__unbounded_nonlinear $'2\n1\n3' <<'EOF'
fn konst(x: a, y: b) -> a = x

fn main() -> Unit !{IO} =
  do
    match dup(1) with | (p, q) -> print(int_to_string(p + q))
    print(int_to_string(konst(1, "s")))
    print(int_to_string(list_length([1, 2, 3])))
EOF
acc "$HDR_L" body__swap $'5\n3' <<'EOF'
fn swap(p: (a, b)) -> (b, a) where linear a, linear b = match p with | (x, y) -> (y, x)

fn main() -> Unit !{IO} =
  match swap((L(3), L(5))) with
  | (m, n) ->
    do
      print(int_to_string(close(m)))
      print(int_to_string(close(n)))
EOF
acc "$HDR_L" body__borrow $'7\n5' <<'EOF'
fn view(x: borrowing a) -> Int where linear a = 7

fn main() -> Unit !{IO} =
  do
    let l = L(5)
    print(int_to_string(view(l)))
    print(int_to_string(close(l)))
EOF
acc "$HDR_L" annsib__both_bounded '4' <<'EOF'
fn ping(x: a, n: Int) -> a where linear a = pong(x, n)

fn pong(y: a, n: Int) -> a where linear a = if n > 0 then ping(y, n - 1) else y

fn main() -> Unit !{IO} = print(int_to_string(close(ping(L(4), 2))))
EOF
acc "$HDR_L" unannsib__once '4' <<'EOF'
fn ping(x: a, n: Int) -> a where linear a = pong(x, n)

fn pong(y, n: Int) = if n > 0 then ping(y, n - 1) else y

fn main() -> Unit !{IO} = print(int_to_string(close(ping(L(4), 2))))
EOF
acc "$HDR_L" unannsib__pass_to_bounded '4' <<'EOF'
fn keep(z: b) -> b where linear b = z

fn ping(x: a, n: Int) -> a where linear a = pong(x, n)

fn pong(y, n: Int) = if n > 0 then ping(y, n - 1) else keep(y)

fn main() -> Unit !{IO} = print(int_to_string(close(ping(L(4), 2))))
EOF
acc "$HDR_N" unannsib_nolin__once '4' <<'EOF'
fn ping(x: a, n: Int) -> a where linear a = pong(x, n)

fn pong(y, n: Int) = if n > 0 then ping(y, n - 1) else y

fn main() -> Unit !{IO} = print(int_to_string(ping(4, 2)))
EOF
acc "$HDR_N" unannsib_nolin__pass_to_bounded '4' <<'EOF'
fn keep(z: b) -> b where linear b = z

fn ping(x: a, n: Int) -> a where linear a = pong(x, n)

fn pong(y, n: Int) = if n > 0 then ping(y, n - 1) else keep(y)

fn main() -> Unit !{IO} = print(int_to_string(ping(4, 2)))
EOF
acc "$HDR_L" inst__nonlinear_head_copy '4' <<'EOF'
class Dup2 t
  fn dup2(x: t) -> (t, t)

instance Dup2 (Maybe a)
  fn dup2(x: Maybe a) -> (Maybe a, Maybe a) = (x, x)

fn eat(m: Maybe Int) -> Int = match m with | Just n -> n + 1 | Nothing -> 0

fn main() -> Unit !{IO} = match dup2(Just(1)) with | (p, q) -> print(int_to_string(eat(p) + eat(q)))
EOF
acc "$HDR_L" inst__consume_once '6' <<'EOF'
class Take t
  fn take(x: t) -> t

instance Take (Maybe a) where linear a
  fn take(x: Maybe a) -> Maybe a = x

fn main() -> Unit !{IO} = print(int_to_string(match take(Just (L(6))) with | Just l -> close(l) | Nothing -> 0))
EOF
acc "$HDR_L" inst__nested_bounded '5' <<EOF
$TAKER
instance Taker (Box a) where linear a, Taker a
  fn take(x: Box a) -> Int = match x with | Box y -> take(y)

instance Taker (Wrap a) where linear a, Taker a
  fn take(x: Wrap a) -> Int = match x with | Wrap y -> take(y)

fn main() -> Unit !{IO} = print(int_to_string(take(Box (Wrap (L(5))))))
EOF
# An unbounded head variable is non-linear: discarding one is fine (§6.4).
acc "$HDR_L" inst__head_var_discard '0' <<'EOF'
class Count t
  fn count(x: t) -> Int

fn first(m: Maybe a) -> a = match m with | Just v -> v | Nothing -> panic("none")

instance Count (Maybe a)
  fn count(x: Maybe a) -> Int =
    do
      first(x)
      0

fn main() -> Unit !{IO} = print(int_to_string(count(Just(1))))
EOF
acc "$HDR_L" inst__head_var_wild '0' <<'EOF'
class Count t
  fn count(x: t) -> Maybe Int

instance Count (Maybe a)
  fn count(x: Maybe a) -> Maybe Int =
    do
      _ <- x
      Just(0)

fn main() -> Unit !{IO} = print(match count(Just(1)) with | Just n -> int_to_string(n) | Nothing -> "none")
EOF
acc "$HDR_L" instb__once $'3\n4' <<'EOF'
class Take t
  fn take(x: t) -> t

instance Take (Maybe a) where linear a
  fn take(x: Maybe a) -> Maybe a = match x with | Just y -> Just(y) | Nothing -> Nothing

fn main() -> Unit !{IO} =
  do
    print(int_to_string(match take(Just (L(3))) with | Just l -> close(l) | Nothing -> 0))
    print(int_to_string(match take(Just(4)) with | Just n -> n | Nothing -> 0))
EOF
acc "$HDR_N" instb_nolin__once '4' <<'EOF'
class Take t
  fn take(x: t) -> t

instance Take (Maybe a) where linear a
  fn take(x: Maybe a) -> Maybe a = match x with | Just y -> Just(y) | Nothing -> Nothing

fn main() -> Unit !{IO} = print(int_to_string(match take(Just(4)) with | Just n -> n | Nothing -> 0))
EOF
# The prelude instances that take the bound, at a linear type: `ToString` over
# `Maybe`, `Result` and tuples, and `try` with a linear error.
acc "$HDR_L" inst__prelude_opt_ins $'Just(L)\n(L, 2)\nOk(L)\n4' <<'EOF'
instance ToString L
  fn to_string(x: L) -> String = match x with | L _ -> "L"

fn ok(l: L) -> Result String L = Ok(l)

fn bump(r: Result L Int) -> Result L Int =
  let n = try r
  in Ok(n + 1)

fn main() -> Unit !{IO} =
  do
    print(to_string(Just (L(1))))
    print(to_string((L(1), 2)))
    print(to_string(ok(L(3))))
    print(match bump(Err (L(4))) with | Ok n -> int_to_string(n) | Err l -> int_to_string(close(l)))
EOF
acc "$HDR_L" method__own_var_discard '3' <<'EOF'
class Apply t
  fn ap1(x: t, g: Int -> a) -> Int

instance Apply Int
  fn ap1(x: Int, g: Int -> a) -> Int =
    do
      g(x)
      x

fn main() -> Unit !{IO} = print(int_to_string(ap1(3, \n -> n + 1)))
EOF
acc "$HDR_L" mbound__once $'3\n4' <<'EOF'
class Wrap f
  fn wrap1(x: a) -> f a where linear a

instance Wrap Maybe
  fn wrap1(x: a) -> Maybe a where linear a = Just(x)

fn main() -> Unit !{IO} =
  do
    print(int_to_string(match wrap1(L(3)) with | Just l -> close(l) | Nothing -> 0))
    print(int_to_string(match wrap1(4) with | Just n -> n | Nothing -> 0))
EOF
acc "$HDR_N" mbound_nolin__once '4' <<'EOF'
class Wrap f
  fn wrap1(x: a) -> f a where linear a

instance Wrap Maybe
  fn wrap1(x: a) -> Maybe a where linear a = Just(x)

fn main() -> Unit !{IO} = print(int_to_string(match wrap1(4) with | Just n -> n | Nothing -> 0))
EOF
acc "$HDR_L" ext__panic_at_linear '2' <<'EOF'
fn pick(n: Int) -> L = if n > 0 then L(n) else panic("neg")

fn main() -> Unit !{IO} = print(int_to_string(close(pick(2))))
EOF
acc "$HDR_B" ext__borrowing_decl 'ok' <<'EOF'
extern fn peek_any(x: borrowing a) -> Int where linear a

fn main() -> Unit !{IO} = print("ok")
EOF
acc "$HDR_B" ext__redeclared_same 'ok' <<'EOF'
extern fn give_up(msg: String) -> a where linear a
extern fn give_up(msg: String) -> a where linear a

fn main() -> Unit !{IO} = print("ok")
EOF
acc "$HDR_B" ext__chan_select '5' <<'EOF'
import stdlib.task as task
import stdlib.chan as chan

type linear L = L Int

fn eat(l: L) -> Int = match l with | L n -> n

fn first_ready(s: task.Scope) -> Int !{IO} =
  do
    a <- chan.chan_new(s, 1)
    b <- chan.chan_new(s, 1)
    chan.chan_send(b, L(5))
    chan.chan_close(a)
    match chan.chan_select([b, a]) with
    | (i, chan.Got l) -> i + eat(l)
    | (_, chan.Closed) -> -1

fn main() -> Unit !{IO} = print(int_to_string(task.with_scope(first_ready)))
EOF
acc "$HDR_L" ctor__consume_once $'6\n7' <<'EOF'
type Box a = Box a

fn main() -> Unit !{IO} =
  do
    print(int_to_string(match Box(L(6)) with | Box l -> close(l)))
    print(int_to_string(match Just(L(7)) with | Just l -> close(l) | Nothing -> 0))
EOF
acc "$HDR_L" exist__bounded_once $'4\n5' <<EOF
$EXB
fn main() -> Unit !{IO} =
  do
    print(int_to_string(unbox(Boxed (L(4)))))
    print(int_to_string(unbox(Boxed(5))))
EOF
acc "$HDR_L" exist__let_unpack_once '6' <<EOF
$EXB
let k: Int = match Boxed(L(6)) with | Boxed x -> take(x)

fn main() -> Unit !{IO} = print(int_to_string(k))
EOF
acc "$HDR_L" exist__nonlinear '2' <<'EOF'
class Taker a
  fn take(x: a) -> Int

instance Taker Int
  fn take(x: Int) -> Int = x

type Boxed = | exists a. Boxed a where Taker a

fn unbox(b: Boxed) -> Int = match b with | Boxed x -> take(x)

fn main() -> Unit !{IO} =
  do
    let b = Boxed(1)
    print(int_to_string(unbox(b) + unbox(b)))
EOF
acc "$HDR_L" tlet__nonlinear_and_fn $'2\n5' <<'EOF'
let dup0 = \x -> (x, x)

fn ident(x: a) -> a where linear a = x

fn main() -> Unit !{IO} =
  do
    match dup0(1) with | (p, q) -> print(int_to_string(p + q))
    print(int_to_string(close(ident(L(5)))))
EOF
