#!/usr/bin/env python3
"""Generate tests/stdlib/test_modular_vectors.spr from Python's int as the oracle.

    python3 scripts/gen_modular_vectors.py > tests/stdlib/test_modular_vectors.spr

Vendored rather than generated at test time, for the same reason as
`gen_bigint_vectors.py`: `just test` must not depend on a Python interpreter.

Moduli are unconstrained positives, not primes. That is deliberate — `mod_inv` has
to answer `Nothing` on a non-coprime argument, and a suite of prime moduli never
reaches that arm. Python's `%` is floored, which agrees with this module's Euclidean
residue for every positive modulus, so the oracle needs no adjustment.
"""
import math
import random

SEED = 20260923
COUNT = 20

random.seed(SEED)


def sample(signed=True):
    kind = random.randrange(4)
    if kind == 0:
        value = random.randrange(1, 100)
    elif kind == 1:
        value = random.getrandbits(random.choice([25, 26, 27, 52, 53, 64, 78]))
    elif kind == 2:
        value = random.getrandbits(random.randrange(1, 300))
    else:
        value = random.getrandbits(random.randrange(200, 520))
    return value * (random.choice([1, -1]) if signed else 1)


cases = []


def emit(label, expr, expected):
    cases.append('    check_eq("%s", %s, "%s")' % (label, expr, expected))


for index in range(COUNT):
    m = sample(signed=False) or 1
    a, b = sample(), sample()
    exponent = abs(sample())
    modulus = 'km(parse("%d"))' % m
    emit("reduce %d" % index,
         'show(mod.reduce(parse("%d"), %s))' % (a, modulus), a % m)
    emit("add %d" % index,
         'show(mod.mod_add(parse("%d"), parse("%d"), %s))' % (a, b, modulus), (a + b) % m)
    emit("sub %d" % index,
         'show(mod.mod_sub(parse("%d"), parse("%d"), %s))' % (a, b, modulus), (a - b) % m)
    emit("mul %d" % index,
         'show(mod.mod_mul(parse("%d"), parse("%d"), %s))' % (a, b, modulus), (a * b) % m)
    emit("pow %d" % index,
         'maybe(mod.mod_pow(parse("%d"), parse("%d"), %s))' % (a, exponent, modulus),
         pow(a, exponent, m))
    emit("inv %d" % index,
         'maybe(mod.mod_inv(parse("%d"), %s))' % (a, modulus),
         pow(a % m, -1, m) if math.gcd(a % m, m) == 1 else "none")

print('''import stdlib.test (check_eq, run_suite)
import stdlib.math.bigint as big
import stdlib.math.modular as mod

# GENERATED — do not hand-edit. Regenerate with:
#   python3 scripts/gen_modular_vectors.py > tests/stdlib/test_modular_vectors.spr
#
# %d random (value, modulus) draws per operation, oracle = Python's int, seed %d.
# Moduli are arbitrary positives rather than primes, so `mod_inv`'s non-coprime
# `Nothing` arm is exercised as often as its success arm.

fn parse(raw: String) -> big.BigInt =
  match big.from_string(raw) with
  | Ok value -> value
  | Err _ -> big.from_int(0)

fn km(value: big.BigInt) -> mod.Modulus =
  match mod.modulus(value) with
  | Just m -> m
  | Nothing -> panic("test_modular_vectors: a generated modulus was not positive")

fn show(value: big.BigInt) -> String = big.to_string(value)

fn maybe(value: Maybe big.BigInt) -> String =
  match value with
  | Just inner -> big.to_string(inner)
  | Nothing -> "none"

fn main() -> Unit !{IO} =
  run_suite("modular_vectors", [
%s
  ])''' % (COUNT, SEED, ",\n".join(cases)))
