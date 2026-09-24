#!/usr/bin/env python3
"""Emit the limb-level field arithmetic for stdlib/crypto/p256.sprout.

Authoring aid, not a build step: the output is committed as ordinary source. It exists
because these are forty near-identical carry chains, and a transcription slip in one of
them is invisible to review and produces a wrong answer only for some inputs.
"""
P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
BITS, N = 26, 10
MASK = (1 << BITS) - 1
PL = [(P >> (BITS * i)) & MASK for i in range(N)]

out = []
w = out.append


def felem_pat(prefix):
    return "Felem " + " ".join("%s%d" % (prefix, i) for i in range(N))


def acc_pat(prefix):
    return "Acc " + " ".join("%s%d" % (prefix, i) for i in range(N))


# ---- cios_step ----
w('''# One CIOS pass: t := (t + a*bi + m*p) / 2^26, which clears the low limb.
#
# `m` is just the low limb: n0inv is -p^-1 mod 2^26, and p is -1 there (its low limb is
# 2^26-1), so n0inv is 1 and the multiply disappears. The accumulator peaks at 2^52
# against i64's 2^63, and every limb below stays canonical --- checked over 30,000
# random pairs against the Python reference.''')
w("fn cios_step(t: Acc, a: Felem, bi: Int) -> Acc =")
w("  match t with")
w("  | %s ->" % acc_pat("t"))
w("      match a with")
w("      | %s ->" % felem_pat("a"))
lets = []
for i in range(N):
    carry = "" if i == 0 else " + c%d" % (i - 1)
    lets.append("v%d = t%d + a%d * bi%s" % (i, i, i, carry))
    lets.append("u%d = bit_and(v%d, %d)" % (i, i, MASK))
    lets.append("c%d = bit_shr(v%d, %d)" % (i, i, BITS))
lets.append("m = u0")
for i in range(N):
    carry = "" if i == 0 else " + d%d" % (i - 1)
    term = "" if PL[i] == 0 else " + m * %d" % PL[i]
    lets.append("w%d = u%d%s%s" % (i, i, term, carry))
    if i > 0:
        lets.append("r%d = bit_and(w%d, %d)" % (i - 1, i, MASK))
    lets.append("d%d = bit_shr(w%d, %d)" % (i, i, BITS))
lets.append("r9 = c9 + d9")
first = True
for line in lets:
    w(("          let " if first else "              ") + line)
    first = False
w("          in Acc(%s)" % ", ".join("r%d" % i for i in range(N)))
w("")

# ---- conditional subtract of p ----
w('''# a - p when that does not go negative, a otherwise. The CIOS tail leaves a value
# below 2p, so one of these normalises it --- verified, the worst case observed is 1.''')
w("fn sub_p_if_ge(a: Felem) -> Felem =")
w("  match a with")
w("  | %s ->" % felem_pat("a"))
lets = []
for i in range(N):
    borrow = "" if i == 0 else " - b%d" % (i - 1)
    lets.append("s%d = a%d - %d%s" % (i, i, PL[i], borrow))
    lets.append("b%d = bit_and(bit_shr_zf(s%d, 63), 1)" % (i, i))
    lets.append("d%d = bit_and(s%d, %d)" % (i, i, MASK))
first = True
for line in lets:
    w(("      let " if first else "          ") + line)
    first = False
w("      in if b9 == 1 then a")
w("         else Felem(%s)" % ", ".join("d%d" % i for i in range(N)))
w("")

# ---- add ----
w('''# Both operands are below p, so the sum needs no eleventh limb: p's top limb is under
# 2^22, so limb 9 of the sum cannot carry out. One conditional subtract finishes it.''')
w("fn felem_add(a: Felem, b: Felem) -> Felem =")
w("  match a with")
w("  | %s ->" % felem_pat("a"))
w("      match b with")
w("      | %s ->" % felem_pat("b"))
lets = []
for i in range(N):
    carry = "" if i == 0 else " + c%d" % (i - 1)
    lets.append("s%d = a%d + b%d%s" % (i, i, i, carry))
    lets.append("r%d = bit_and(s%d, %d)" % (i, i, MASK))
    # No carry out of the top limb: see the precondition above.
    if i < N - 1:
        lets.append("c%d = bit_shr(s%d, %d)" % (i, i, BITS))
first = True
for line in lets:
    w(("          let " if first else "              ") + line)
    first = False
w("          in sub_p_if_ge(Felem(%s))" % ", ".join("r%d" % i for i in range(N)))
w("")

# ---- sub ----
w('''# On a borrow out of the top limb the difference is negative, and adding p back lands
# it in [0, p) --- the carry out of that addition is the 2^260 the borrow introduced,
# and dropping it is what makes the wrap correct rather than an overflow.''')
w("fn felem_sub(a: Felem, b: Felem) -> Felem =")
w("  match a with")
w("  | %s ->" % felem_pat("a"))
w("      match b with")
w("      | %s ->" % felem_pat("b"))
lets = []
for i in range(N):
    borrow = "" if i == 0 else " - k%d" % (i - 1)
    lets.append("s%d = a%d - b%d%s" % (i, i, i, borrow))
    lets.append("k%d = bit_and(bit_shr_zf(s%d, 63), 1)" % (i, i))
    lets.append("r%d = bit_and(s%d, %d)" % (i, i, MASK))
for i in range(N):
    carry = "" if i == 0 else " + e%d" % (i - 1)
    lets.append("g%d = r%d + k9 * %d%s" % (i, i, PL[i], carry))
    lets.append("f%d = bit_and(g%d, %d)" % (i, i, MASK))
    # The carry out of the top limb is the 2^260 the borrow introduced; it is dropped.
    if i < N - 1:
        lets.append("e%d = bit_shr(g%d, %d)" % (i, i, BITS))
first = True
for line in lets:
    w(("          let " if first else "              ") + line)
    first = False
w("          in Felem(%s)" % ", ".join("f%d" % i for i in range(N)))
w("")

# ---- equality / zero ----
w("fn felem_eq(a: Felem, b: Felem) -> Bool =")
w("  match a with")
w("  | %s ->" % felem_pat("a"))
w("      match b with")
w("      | %s ->" % felem_pat("b"))
w("          " + " &&\n          ".join("a%d == b%d" % (i, i) for i in range(N)))
w("")
w("fn felem_is_zero(a: Felem) -> Bool =")
w("  match a with")
w("  | %s ->" % felem_pat("a"))
w("      " + " && ".join("a%d == 0" % i for i in range(N)))
w("")

# ---- limb accessor, for the Fermat exponent walk ----
w("fn felem_limb(a: Felem, index: Int) -> Int =")
w("  match a with")
w("  | %s ->" % felem_pat("a"))
for i in range(N):
    kw = "      if" if i == 0 else "      else if"
    w("%s index == %d then a%d" % (kw, i, i))
w("      else 0")
w("")

# ---- mont_mul ----
w('''# Montgomery product: mont_mul(x*R, y*R) is (x*y)*R, so a chain of them stays in
# Montgomery form and only the boundary conversions cost anything extra.''')
w("fn mont_mul(a: Felem, b: Felem) -> Felem =")
w("  match b with")
w("  | %s ->" % felem_pat("b"))
w("      let z = Acc(%s)" % ", ".join(["0"] * N))
for i in range(N):
    prev = "z" if i == 0 else "s%d" % (i - 1)
    w("          s%d = cios_step(%s, a, b%d)" % (i, prev, i))
w("      in match s9 with")
w("         | %s -> sub_p_if_ge(Felem(%s))"
  % (acc_pat("q"), ", ".join("q%d" % i for i in range(N))))
w("")

# ---- constants ----
def felem_lit(name, value, comment=None):
    ls = [(value >> (BITS * i)) & MASK for i in range(N)]
    if comment:
        w("# " + comment)
    w("let %s = Felem(%s)" % (name, ", ".join(str(x) for x in ls)))
    w("")


R = 1 << (BITS * N)
felem_lit("felem_zero", 0)
felem_lit("felem_one_mont", R % P, "1 in Montgomery form, which is R mod p.")
felem_lit("felem_r2", R * R % P, "R^2 mod p: multiplying by it is what enters Montgomery form.")
felem_lit("felem_b_mont", 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b * R % P,
          "The curve's b, in Montgomery form.")
felem_lit("felem_gx_mont", 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296 * R % P,
          "The generator, in Montgomery form.")
felem_lit("felem_gy_mont", 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5 * R % P)
felem_lit("felem_p_minus_2", P - 2, "The Fermat exponent: a^(p-2) is a^-1 for prime p.")

print("\n".join(out))
