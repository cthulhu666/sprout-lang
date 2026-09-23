#!/usr/bin/env python3
"""Generate tests/stdlib/test_bigint_vectors.spr from Python's int as the oracle.

    python3 scripts/gen_bigint_vectors.py > tests/stdlib/test_bigint_vectors.spr

The vectors are vendored rather than generated at test time: `just test` must not
depend on a Python interpreter, and a fixed seed keeps a failure reproducible.

Widths are not uniform. They cluster on the boundaries where a limb-based bignum
actually breaks — either side of the 26-bit radix and its multiples, top limbs at
base/2 (the normalisation threshold Knuth's quotient estimate is stated against),
and all-ones / all-zeros limb runs, where carry and borrow chains run longest.
"""
import random

SEED = 20260923
COUNT = 30
BASE = 1 << 26

random.seed(SEED)


def sample():
    kind = random.randrange(6)
    if kind == 0:
        bits = random.randrange(1, 27)
    elif kind == 1:
        bits = random.choice([25, 26, 27, 51, 52, 53, 63, 64, 65, 77, 78, 79])
    elif kind == 2:
        bits = random.randrange(1, 300)
    elif kind == 3:
        bits = random.randrange(200, 600)
    elif kind == 4:
        value = random.choice([1, 2, BASE // 2 - 1, BASE // 2, BASE - 1])
        for _ in range(random.randrange(1, 12) - 1):
            value = value * BASE + random.randrange(BASE)
        return value * random.choice([1, -1])
    else:
        value = 0
        for _ in range(random.randrange(1, 12)):
            value = value * BASE + random.choice([0, 1, BASE - 1, BASE - 2])
        return (value or 1) * random.choice([1, -1])
    return random.getrandbits(bits) * random.choice([1, -1])


cases = []


def emit(label, expr, expected):
    cases.append('    check_eq("%s", %s, "%s")' % (label, expr, expected))


def lit(value):
    return 'parse("%d")' % value


for index in range(COUNT):
    a, b = sample(), sample()
    emit("add %d" % index, "big.to_string(big.add(%s, %s))" % (lit(a), lit(b)), a + b)
    emit("sub %d" % index, "big.to_string(big.sub(%s, %s))" % (lit(a), lit(b)), a - b)
    emit("mul %d" % index, "big.to_string(big.mul(%s, %s))" % (lit(a), lit(b)), a * b)
    if b != 0:
        # Truncated division, matching the module: quotient toward zero, remainder
        # signed like the dividend.
        quotient = abs(a) // abs(b)
        rest = abs(a) - quotient * abs(b)
        emit("div %d" % index, "quot(%s, %s)" % (lit(a), lit(b)),
             quotient if (a < 0) == (b < 0) else -quotient)
        emit("rem %d" % index, "rest(%s, %s)" % (lit(a), lit(b)),
             -rest if a < 0 else rest)
    count = random.randrange(0, 200)
    emit("shl %d" % index, "big.to_string(big.shl(%s, %d))" % (lit(a), count),
         a * (1 << count))
    shifted = abs(a) >> count
    emit("shr %d" % index, "big.to_string(big.shr(%s, %d))" % (lit(a), count),
         -shifted if (a < 0 and shifted) else shifted)
    emit("hex %d" % index, "big.to_hex(%s)" % lit(a),
         ("-0x%x" % -a) if a < 0 else ("0x%x" % a))
    emit("hex parse %d" % index,
         'big.to_string(parse("%s"))' % (("-0x%X" % -a) if a < 0 else ("0x%X" % a)), a)
    emit("bit_length %d" % index, "int_to_string(big.bit_length(%s))" % lit(a),
         abs(a).bit_length())
    width = (abs(a).bit_length() + 7) // 8 + random.randrange(0, 3)
    emit("bytes %d" % index, "byte_roundtrip(%s, %d)" % (lit(abs(a)), width), abs(a))

print('''import stdlib.test (check_eq, run_suite)
import stdlib.math.bigint as big

# GENERATED — do not hand-edit. Regenerate with:
#   python3 scripts/gen_bigint_vectors.py > tests/stdlib/test_bigint_vectors.spr
#
# %d random values per operation, oracle = Python's arbitrary-precision int, seed
# %d. The hand-written cases in test_bigint.spr pin the named edges; these cover
# the limb-boundary and carry-chain shapes no one thinks to write by hand.

fn parse(raw: String) -> big.BigInt =
  match big.from_string(raw) with
  | Ok value -> value
  | Err _ -> big.from_int(0)

fn quot(left: big.BigInt, right: big.BigInt) -> String =
  match big.divmod(left, right) with
  | Just ((quotient, _)) -> big.to_string(quotient)
  | Nothing -> "none"

fn rest(left: big.BigInt, right: big.BigInt) -> String =
  match big.divmod(left, right) with
  | Just ((_, remainder)) -> big.to_string(remainder)
  | Nothing -> "none"

fn byte_roundtrip(value: big.BigInt, width: Int) -> String =
  match big.to_bytes_be(value, width) with
  | Just raw -> big.to_string(big.from_bytes_be(raw))
  | Nothing -> "none"

fn main() -> Unit !{IO} =
  run_suite("bigint_vectors", [
%s
  ])''' % (COUNT, SEED, ",\n".join(cases)))
