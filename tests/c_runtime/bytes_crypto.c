/* The bytes and crypto builtins hand Sprout headered Strings and well-formed Bytes / Builder
 * values (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that leaves through sprout_str_word or enters
 * through sprout_str_of_word is header-checked, and str_eq / str_byte_len check each payload again.
 * The runtime is included, not linked, so main can setenv before the first HDRCHECK query.
 * argv[1] = "stress" also sets SPROUT_GC_STRESS=1: every allocation collects, so an input a
 * function fails to root across its own allocations is freed and fails the byte check. Interned
 * inputs are never freed; bytes_from_utf8 and crypto_base64_decode also get an unrooted one (F).
 *
 * Covered: bytes_empty, bytes_slice (with clamping), bytes_append, bytes_singleton,
 * bytes_from_utf8, bytes_to_utf8 (Ok text, and the Err(Utf8DecodeError) text), every
 * bytes_builder_* constructor and bytes_builder_build; crypto_sha256, crypto_hmac_sha256,
 * crypto_base64_encode, crypto_base64_decode (Ok, and each Err text), crypto_bytes_xor (Ok and the
 * length mismatch), crypto_random_bytes (length, and the negative count).
 * Not covered: the null-input and out-of-range aborts (sprout_fail), crypto_random_bytes's
 * /dev/urandom failure branches, and out-of-memory branches.
 */
#include <stdio.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"
#include "str_check.h"

enum { ERR = 1, OK = 2, UTF8_ERR = 3, B64_ERR = 4, XOR_MISMATCH = 5, CRYPTO_INVALID = 6 };

/* `b` is a Bytes block holding exactly the `n` bytes at `data`. */
static int bytes_is(long long b, const void* data, size_t n) {
  if (b == 0 || sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)b)) != SPROUT_HEAP_BYTES) return 0;
  const BytesVal* v = (const BytesVal*)(uintptr_t)b;
  return v->len == n && (n == 0 || memcmp(v->data, data, n) == 0);
}
static int bytes_is_text(long long b, const char* text) { return bytes_is(b, text, strlen(text)); }

/* `b` is a Builder block of `len` bytes in `count` chunks. */
static int builder_is(long long b, size_t len, size_t count) {
  if (b == 0 || sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)b)) != SPROUT_HEAP_BUILDER) return 0;
  const BuilderVal* v = (const BuilderVal*)(uintptr_t)b;
  return v->len == len && v->count == count;
}

static long long B(const char* text) { return bytes_from_utf8(I(text)); }

/* `r` is Ok(Bytes of `data`). */
static int ok_bytes(long long r, const void* data, size_t n) {
  return sprout_tag(r) == OK && bytes_is(sprout_field(r, 0), data, n);
}

/* `r` is Err(<ctor>(<String>)); the String is `want`, or any non-empty text when NULL. */
static int err_text(long long r, long long ctor, const char* want) {
  if (sprout_tag(r) != ERR) return 0;
  long long e = sprout_field(r, 0);
  return sprout_tag(e) == ctor && is_str(sprout_field(e, 0), want);
}

static void hex_of(long long b, char out[65]) {
  const BytesVal* v = (const BytesVal*)(uintptr_t)b;
  for (size_t i = 0; i < v->len && i < 32; i++) snprintf(out + 2 * i, 3, "%02x", v->data[i]);
}

static int hex_is(long long b, const char* want) {
  char got[65] = "";
  if (((const BytesVal*)(uintptr_t)b)->len != strlen(want) / 2) return 0;
  hex_of(b, got);
  return strcmp(got, want) == 0;
}

static void run_bytes(void) {
  check(bytes_is_text(bytes_empty(), ""), "bytes_empty");

  check(bytes_is_text(bytes_from_utf8(I("")), ""), "bytes_from_utf8(\"\")");
  check(bytes_is_text(bytes_from_utf8(I("h\xc3\xa9llo")), "h\xc3\xa9llo"), "bytes_from_utf8");
  check(bytes_is_text(bytes_from_utf8(F("unrooted input")), "unrooted input"),
        "bytes_from_utf8 of an unrooted String");

  check(bytes_is(bytes_singleton(65), "A", 1), "bytes_singleton(65)");
  check(bytes_is(bytes_singleton(0), "\0", 1), "bytes_singleton(0)");
  check(bytes_is(bytes_singleton(255), "\xff", 1), "bytes_singleton(255)");

  SPROUT_HANDLE(h_base, B("abcdef"));
  long long base = sprout_handle_get(h_base);
  check(bytes_is_text(bytes_slice(base, 1, 3), "bcd"), "bytes_slice(1, 3)");
  check(bytes_is_text(bytes_slice(base, 0, 6), "abcdef"), "bytes_slice whole");
  check(bytes_is_text(bytes_slice(base, 4, 99), "ef"), "bytes_slice count clamps");
  check(bytes_is_text(bytes_slice(base, -5, 2), "ab"), "bytes_slice start clamps to 0");
  check(bytes_is_text(bytes_slice(base, 99, 3), ""), "bytes_slice past the end");
  check(bytes_is_text(bytes_slice(base, 6, 1), ""), "bytes_slice at the end");
  check(bytes_is_text(bytes_slice(base, 2, -1), ""), "bytes_slice(count -1)");
  check(bytes_is_text(bytes_slice(base, 2, 0), ""), "bytes_slice of length 0");

  SPROUT_HANDLE(h_ab, B("ab"));
  SPROUT_HANDLE(h_cd, B("cd"));
  SPROUT_HANDLE(h_none, bytes_empty());
  long long ab = sprout_handle_get(h_ab);
  long long cd = sprout_handle_get(h_cd);
  long long none = sprout_handle_get(h_none);
  check(bytes_is_text(bytes_append(ab, cd), "abcd"), "bytes_append");
  check(bytes_is_text(bytes_append(ab, none), "ab"), "bytes_append(x, empty)");
  check(bytes_is_text(bytes_append(none, cd), "cd"), "bytes_append(empty, x)");
  check(bytes_is_text(bytes_append(none, none), ""), "bytes_append(empty, empty)");

  /* Ok text */
  long long r = bytes_to_utf8(none);
  check(sprout_tag(r) == OK && is_str(sprout_field(r, 0), ""), "bytes_to_utf8(empty) is Ok(\"\")");
  r = bytes_to_utf8(B("h\xc3\xa9llo\xe6\x97\xa5"));
  check(sprout_tag(r) == OK && is_str(sprout_field(r, 0), "h\xc3\xa9llo\xe6\x97\xa5"),
        "bytes_to_utf8 Ok text");

  /* Err(Utf8DecodeError) text, one per reason */
  check(err_text(bytes_to_utf8(bytes_singleton(0xff)), UTF8_ERR, "invalid UTF-8 sequence"),
        "bytes_to_utf8(0xff) is Err(Utf8DecodeError \"invalid UTF-8 sequence\")");
  check(err_text(bytes_to_utf8(bytes_singleton(0xc3)), UTF8_ERR, "truncated UTF-8 sequence"),
        "bytes_to_utf8(lone lead byte) is Err(Utf8DecodeError \"truncated UTF-8 sequence\")");
  check(err_text(bytes_to_utf8(bytes_singleton(0)), UTF8_ERR, "decoded string contains NUL byte"),
        "bytes_to_utf8(NUL) is Err(Utf8DecodeError \"decoded string contains NUL byte\")");
}

static void run_builders(void) {
  check(builder_is(bytes_builder_empty(), 0, 0), "bytes_builder_empty");
  check(bytes_is_text(bytes_builder_build(bytes_builder_empty()), ""), "build(empty builder)");

  check(builder_is(bytes_builder_bytes(B("xy")), 2, 1), "bytes_builder_bytes");
  check(bytes_is_text(bytes_builder_build(bytes_builder_bytes(B("xy"))), "xy"),
        "build(bytes_builder_bytes)");
  check(builder_is(bytes_builder_bytes(bytes_empty()), 0, 0), "bytes_builder_bytes(empty)");

  check(builder_is(bytes_builder_byte(0x41), 1, 1), "bytes_builder_byte");
  check(bytes_is_text(bytes_builder_build(bytes_builder_byte(0x41)), "A"), "build(byte)");

  check(builder_is(bytes_builder_u16_be(0x4142), 2, 1), "bytes_builder_u16_be");
  check(bytes_is_text(bytes_builder_build(bytes_builder_u16_be(0x4142)), "AB"), "build(u16_be)");
  check(bytes_is(bytes_builder_build(bytes_builder_u16_be(-1)), "\xff\xff", 2),
        "build(u16_be(-1)) wraps");
  check(bytes_is(bytes_builder_build(bytes_builder_u16_be(0x10001)), "\x00\x01", 2),
        "build(u16_be(0x10001)) wraps");

  check(builder_is(bytes_builder_u32_be(0x41424344), 4, 1), "bytes_builder_u32_be");
  check(bytes_is_text(bytes_builder_build(bytes_builder_u32_be(0x41424344)), "ABCD"),
        "build(u32_be)");
  check(bytes_is(bytes_builder_build(bytes_builder_u32_be(-1)), "\xff\xff\xff\xff", 4),
        "build(u32_be(-1)) wraps");

  SPROUT_HANDLE(h_a, bytes_builder_byte('a'));
  SPROUT_HANDLE(h_bc, bytes_builder_u16_be(('b' << 8) | 'c'));
  SPROUT_HANDLE(h_none, bytes_builder_empty());
  long long a = sprout_handle_get(h_a);
  long long bc = sprout_handle_get(h_bc);
  long long none = sprout_handle_get(h_none);
  SPROUT_HANDLE(h_abc, bytes_builder_append(a, bc));
  long long abc = sprout_handle_get(h_abc);
  check(builder_is(abc, 3, 2), "bytes_builder_append");
  check(bytes_is_text(bytes_builder_build(abc), "abc"), "build(append)");
  check(bytes_builder_append(none, a) == a, "bytes_builder_append(empty, x) is x");
  check(bytes_builder_append(a, none) == a, "bytes_builder_append(x, empty) is x");
  check(builder_is(bytes_builder_append(none, none), 0, 0), "bytes_builder_append(empty, empty)");
  SPROUT_HANDLE(h_abca, bytes_builder_append(abc, a));
  check(bytes_is_text(bytes_builder_build(sprout_handle_get(h_abca)), "abca"),
        "build(append of an append)");
}

static void run_crypto(void) {
  SPROUT_HANDLE(h_abc, B("abc"));
  long long abc = sprout_handle_get(h_abc);

  check(hex_is(crypto_sha256(abc),
               "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
        "crypto_sha256(\"abc\")");
  check(hex_is(crypto_sha256(bytes_empty()),
               "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        "crypto_sha256(\"\")");

  /* RFC 4231 test case 2 */
  SPROUT_HANDLE(h_key, B("Jefe"));
  SPROUT_HANDLE(h_msg, B("what do ya want for nothing?"));
  check(hex_is(crypto_hmac_sha256(sprout_handle_get(h_key), sprout_handle_get(h_msg)),
               "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"),
        "crypto_hmac_sha256 (RFC 4231 case 2)");

  /* base64 encode: every padding length */
  check(is_str(crypto_base64_encode(abc), "YWJj"), "crypto_base64_encode(abc)");
  check(is_str(crypto_base64_encode(B("ab")), "YWI="), "crypto_base64_encode(ab)");
  check(is_str(crypto_base64_encode(B("a")), "YQ=="), "crypto_base64_encode(a)");
  check(is_str(crypto_base64_encode(bytes_empty()), ""), "crypto_base64_encode(empty)");
  check(is_str(crypto_base64_encode(bytes_builder_build(bytes_builder_u32_be(-1))), "/////w=="),
        "crypto_base64_encode(ff ff ff ff)");

  /* base64 decode: the inputs are unrooted Strings */
  check(ok_bytes(crypto_base64_decode(F("YWJj")), "abc", 3), "crypto_base64_decode(YWJj)");
  check(ok_bytes(crypto_base64_decode(F("YWI=")), "ab", 2), "crypto_base64_decode(YWI=)");
  check(ok_bytes(crypto_base64_decode(F("YQ==")), "a", 1), "crypto_base64_decode(YQ==)");
  check(ok_bytes(crypto_base64_decode(F("")), "", 0), "crypto_base64_decode(\"\")");
  check(ok_bytes(crypto_base64_decode(F("/////w==")), "\xff\xff\xff\xff", 4),
        "crypto_base64_decode(/////w==)");
  check(ok_bytes(crypto_base64_decode(crypto_base64_encode(B("round trip"))), "round trip", 10),
        "base64 round trip");
  check(err_text(crypto_base64_decode(F("abc")), B64_ERR, "invalid base64 length"),
        "crypto_base64_decode(bad length)");
  check(err_text(crypto_base64_decode(F("ab!d")), B64_ERR, "invalid base64 character"),
        "crypto_base64_decode(bad character)");
  check(err_text(crypto_base64_decode(F("YQ=A")), B64_ERR, "invalid base64 padding"),
        "crypto_base64_decode(bad padding)");
  check(err_text(crypto_base64_decode(F("YQ==YQ==")), B64_ERR, "invalid base64 padding"),
        "crypto_base64_decode(padding before the end)");

  /* xor */
  SPROUT_HANDLE(h_x, bytes_builder_build(bytes_builder_u32_be(0x01020304)));
  SPROUT_HANDLE(h_y, bytes_builder_build(bytes_builder_u32_be(-1)));
  SPROUT_HANDLE(h_short, B("ab"));
  long long x = sprout_handle_get(h_x);
  long long y = sprout_handle_get(h_y);
  check(ok_bytes(crypto_bytes_xor(x, y), "\xfe\xfd\xfc\xfb", 4), "crypto_bytes_xor");
  SPROUT_HANDLE(h_none, bytes_empty());
  long long none = sprout_handle_get(h_none);
  check(ok_bytes(crypto_bytes_xor(none, none), "", 0), "crypto_bytes_xor(empty, empty)");
  long long m = crypto_bytes_xor(x, sprout_handle_get(h_short));
  check(sprout_tag(m) == ERR && sprout_tag(sprout_field(m, 0)) == XOR_MISMATCH &&
        sprout_field(sprout_field(m, 0), 0) == 4 && sprout_field(sprout_field(m, 0), 1) == 2,
        "crypto_bytes_xor length mismatch is Err(BytesXorLengthMismatch 4 2)");

  /* random */
  long long r = crypto_random_bytes(16);
  check(sprout_tag(r) == OK && ((const BytesVal*)(uintptr_t)sprout_field(r, 0))->len == 16,
        "crypto_random_bytes(16) has 16 bytes");
  check(ok_bytes(crypto_random_bytes(0), "", 0), "crypto_random_bytes(0)");
  check(err_text(crypto_random_bytes(-1), CRYPTO_INVALID, "count must be >= 0"),
        "crypto_random_bytes(-1) is Err(CryptoInvalidArgument)");
}

int main(int argc, char** argv) {
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  if (argc == 2 && strcmp(argv[1], "stress") == 0) setenv("SPROUT_GC_STRESS", "1", 1);
  else if (argc != 1) return 99;
  sprout_register_ctor(ERR, "Err", 1, "p");
  sprout_register_ctor(OK, "Ok", 1, "p");
  sprout_register_ctor(UTF8_ERR, "stdlib.bytes.Utf8DecodeError", 1, "s");
  sprout_register_ctor(B64_ERR, "stdlib.crypto.Base64DecodeError", 1, "s");
  sprout_register_ctor(XOR_MISMATCH, "stdlib.crypto.BytesXorLengthMismatch", 2, "ii");
  sprout_register_ctor(CRYPTO_INVALID, "stdlib.crypto.CryptoInvalidArgument", 1, "s");
  run_bytes();
  run_builders();
  run_crypto();
  if (g_failed) return 1;
  printf("bytes-crypto%s\n", argc == 2 ? "-stress" : "");
  return 0;
}
