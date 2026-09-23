#!/usr/bin/env python3
"""Generate tests/stdlib/test_p256_vectors.spr from Wycheproof's ECDSA P-256 suite.

    curl -sSO https://raw.githubusercontent.com/C2SP/wycheproof/$REV/testvectors_v1/ecdsa_secp256r1_sha256_test.json
    python3 scripts/gen_p256_vectors.py ecdsa_secp256r1_sha256_test.json \
        > tests/stdlib/test_p256_vectors.spr

Vendored rather than fetched at test time, for the same reason as
`gen_bigint_vectors.py` and `gen_modular_vectors.py`: `just test` must not depend on
a network or on a Python interpreter.

The upstream path moved. Wycheproof's `testvectors/` directory was removed and the
suite now lives in `testvectors_v1/`, under the `ecdsa_verify_schema_v1.json` schema
-- docs/bigint-v0.md cited the old path. REV below pins the revision the vendored
file was generated from, so a regeneration that changes the vector count is visible
as a deliberate bump rather than as upstream drift.

Every group in this suite is secp256r1 + SHA-256 + EcdsaVerify with a 65-byte
uncompressed key, and every test's expected result is exactly "valid" or "invalid" --
there is no "acceptable" tier to interpret. The generator asserts all of that rather
than assuming it, because a future revision that adds a group shape would otherwise
emit silently wrong tests.
"""
import json
import sys

REV = "878e5366008753df2064d40c49f8e2f50f9c6af7"  # 2026-05-12
SOURCE = "testvectors_v1/ecdsa_secp256r1_sha256_test.json"


def escape(label):
    return label.replace("\\", "").replace('"', "'")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1]) as handle:
        suite = json.load(handle)

    cases = []
    valid = 0
    for group in suite["testGroups"]:
        key = group["publicKey"]
        if group["type"] != "EcdsaVerify" or group["sha"] != "SHA-256":
            sys.exit("unexpected group type/hash: %r %r" % (group["type"], group["sha"]))
        if key["curve"] != "secp256r1":
            sys.exit("unexpected curve: %r" % key["curve"])
        uncompressed = key["uncompressed"]
        if len(uncompressed) != 130 or not uncompressed.startswith("04"):
            sys.exit("unexpected public key encoding: %r" % uncompressed[:8])
        for test in group["tests"]:
            if test["result"] not in ("valid", "invalid"):
                sys.exit("unexpected result tier: %r" % test["result"])
            check = "check_true" if test["result"] == "valid" else "check_false"
            valid += test["result"] == "valid"
            label = "tc%d %s" % (test["tcId"], escape(test["comment"]))
            cases.append('    %s("%s",\n               v("%s", "%s", "%s"))'
                         % (check, label, uncompressed, test["msg"], test["sig"]))

    rejected = len(cases) - valid
    print(HEADER % (REV, SOURCE, len(cases), valid, rejected, rejected))
    print(",\n".join(cases))
    print("  ])")


HEADER = '''import stdlib.test (check_true, check_false, run_suite)
import stdlib.bytes as bytes
import stdlib.string as string
import stdlib.crypto.p256 as p256

# GENERATED --- do not hand-edit. Regenerate with:
#   python3 scripts/gen_p256_vectors.py <ecdsa_secp256r1_sha256_test.json> \\
#       > tests/stdlib/test_p256_vectors.spr
#
# Project Wycheproof, revision %s
#   %s
#
# %d vectors: %d the curve accepts, %d it must reject. docs/bigint-v0.md Stage 4.
#
# The rejections are the point. Most are DER encodings one byte away from a signature
# that verifies, so a lenient implementation passes every "valid" case and fails these.

fn nibble(raw: String, index: Int) -> Int =
  match string.hex_digit_value(string.char_at_or(raw, index, 'x')) with
  | Just value -> value
  | Nothing -> panic("test_p256_vectors: a vector held a non-hex character")

# Halved rather than appended a byte at a time: `bytes.builder_append` copies both
# sides' chunk arrays, and the longest signature below is 4,172 bytes.
fn hex_span(raw: String, low: Int, high: Int) -> Builder =
  if high <= low then bytes.builder_empty()
  else if high - low == 1 then
    bytes.builder_byte(nibble(raw, low * 2) * 16 + nibble(raw, low * 2 + 1))
  else
    let middle = low + (high - low) / 2
    in bytes.builder_append(hex_span(raw, low, middle), hex_span(raw, middle, high))

fn hex(raw: String) -> Bytes =
  bytes.builder_build(hex_span(raw, 0, string.length(raw) / 2))

# A rejected key PANICS rather than answering `false`: every key here is on the curve,
# so `false` would leave all %d rejection cases passing and blame the wrong function.
fn v(key: String, message: String, der: String) -> Bool =
  match p256.public_key(hex(key)) with
  | Just parsed -> p256.verify(hex(message), hex(der), parsed)
  | Nothing -> panic("test_p256_vectors: a Wycheproof public key was rejected")

fn main() -> Unit !{IO} =
  run_suite("p256_vectors", ['''


main()
