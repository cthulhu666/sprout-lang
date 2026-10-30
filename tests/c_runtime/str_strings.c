/* The string builders hand Sprout headered Strings (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that leaves through sprout_str_word is
 * header-checked, and str_eq / str_byte_len check each payload again on the way in. The runtime
 * is included, not linked, so main can setenv before the first HDRCHECK query and the test can
 * reach sprout_err_string_result and build inputs (interned, hence headered, Strings and Cons
 * lists). argv[1] = "stress" also sets SPROUT_GC_STRESS=1: every allocation collects, so a value
 * a function fails to root across its own allocations is freed and fails the byte check. Interned
 * inputs are never freed; the list builders also get an unrooted one (F), which they must root.
 *
 * Covered: int_to_string, double_to_string, char_to_str, str_concat, string_concat_many,
 * string_join_newlines, str_slice, str_slice_bytes, str_split_lines, split_words, and
 * sprout_err_string_result (directly, and through analysis_check_source).
 * Not covered: the null-input and out-of-range aborts (sprout_fail), char_to_str(0) (a 1-byte
 * block holding an empty text), and sprout_err_string_result's request-failed fallback text,
 * which needs sprout_run_analysis_service to fail with no error string.
 */
#include <math.h>   /* INFINITY */
#include <stdio.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"
#include "str_check.h"

enum { ERR = 1, OK = 2, JUST = 3, NOTHING = 4, CONS = 5, NIL = 6 };

static long long bits_of(double d) {
  long long b;
  memcpy(&b, &d, sizeof b);
  return b;
}

static long long mklist(int n, const char* const* items) {
  SPROUT_HANDLE(h_list, sprout_make0(NIL));
  for (int i = n; i-- > 0;)
    SPROUT_HANDLE_SET(h_list, sprout_make2(CONS, I(items[i]), sprout_handle_get(h_list)));
  return sprout_handle_get(h_list);
}

/* `l` is a Cons list of exactly `n` headered Strings, equal to `want`. */
static int list_is(long long l, int n, const char* const* want) {
  for (int i = 0; i < n; i++) {
    if (sprout_tag(l) != CONS) return 0;
    if (!is_str(sprout_field(l, 0), want[i])) return 0;
    l = sprout_field(l, 1);
  }
  return sprout_tag(l) == NIL;
}

static void run_scalars(void) {
  check(is_str(int_to_string(0), "0"), "int_to_string(0)");
  check(is_str(int_to_string(-7), "-7"), "int_to_string(-7)");
  check(is_str(int_to_string(INT64_MAX), "9223372036854775807"), "int_to_string(INT64_MAX)");
  check(is_str(int_to_string(INT64_MIN), "-9223372036854775808"), "int_to_string(INT64_MIN)");

  check(is_str(double_to_string(bits_of(0.0)), "0.0"), "double_to_string(0.0)");
  check(is_str(double_to_string(bits_of(-0.0)), "-0.0"), "double_to_string(-0.0)");
  check(is_str(double_to_string(bits_of(0.1)), "0.1"), "double_to_string(0.1)");
  check(is_str(double_to_string(bits_of(1.5)), "1.5"), "double_to_string(1.5)");
  check(is_str(double_to_string(bits_of(1e21)), "1e+21"), "double_to_string(1e21)");
  check(is_str(double_to_string(bits_of(INFINITY)), "inf"), "double_to_string(inf)");
  check(is_str(double_to_string(bits_of(-INFINITY)), "-inf"), "double_to_string(-inf)");
  check(is_str(double_to_string(0x7ff8000000000000LL), "nan"), "double_to_string(nan)");

  check(is_str(char_to_str('A'), "A"), "char_to_str('A')");
  check(is_str(char_to_str(0xE9), "\xc3\xa9"), "char_to_str(U+00E9)");
  check(is_str(char_to_str(0x20AC), "\xe2\x82\xac"), "char_to_str(U+20AC)");
  check(is_str(char_to_str(0x1F331), "\xf0\x9f\x8c\xb1"), "char_to_str(U+1F331), 4 bytes");
  check(is_str(char_to_str(0x10FFFF), "\xf4\x8f\xbf\xbf"), "char_to_str(U+10FFFF)");
}

static void run_concat(void) {
  check(is_str(str_concat(I(""), I("")), ""), "str_concat(\"\", \"\")");
  check(is_str(str_concat(I("a"), I("")), "a"), "str_concat(a, \"\")");
  check(is_str(str_concat(I(""), I("b")), "b"), "str_concat(\"\", b)");
  check(is_str(str_concat(I("\xc3\xa9"), I("\xe6\x97\xa5\xe6\x9c\xac")),
               "\xc3\xa9\xe6\x97\xa5\xe6\x9c\xac"), "str_concat(multi-byte)");

  static const char* const none[] = { "" };
  check(is_str(string_concat_many(mklist(0, none)), ""), "string_concat_many([])");
  check(is_str(string_concat_many(mklist(1, none)), ""), "string_concat_many([\"\"])");
  static const char* const abc[] = { "a", "", "bc" };
  check(is_str(string_concat_many(mklist(3, abc)), "abc"), "string_concat_many([a, \"\", bc])");
  static const char* const mb[] = { "\xc3\xa9", "\xf0\x9f\x8c\xb1" };
  check(is_str(string_concat_many(mklist(2, mb)), "\xc3\xa9\xf0\x9f\x8c\xb1"),
        "string_concat_many(multi-byte)");

  check(is_str(string_join_newlines(mklist(0, none)), ""), "string_join_newlines([])");
  check(is_str(string_join_newlines(mklist(1, none)), "\n"), "string_join_newlines([\"\"])");
  static const char* const ab[] = { "a", "b" };
  check(is_str(string_join_newlines(mklist(2, ab)), "a\nb\n"), "string_join_newlines([a, b])");
  check(is_str(string_join_newlines(mklist(2, mb)), "\xc3\xa9\n\xf0\x9f\x8c\xb1\n"),
        "string_join_newlines(multi-byte)");
}

static void run_slices(void) {
  const long long hello = I("h\xc3\xa9llo\xe6\x97\xa5\xe6\x9c\xac"); /* 5 + 2 chars, 12 bytes */
  check(is_str(str_slice(hello, 1, 3), "\xc3\xa9ll"), "str_slice(1, 3) across a 2-byte char");
  check(is_str(str_slice(hello, 5, 2), "\xe6\x97\xa5\xe6\x9c\xac"), "str_slice(5, 2) 3-byte chars");
  check(is_str(str_slice(hello, 6, 1), "\xe6\x9c\xac"), "str_slice(6, 1) last char");
  check(is_str(str_slice(hello, 7, 5), ""), "str_slice at the end");
  check(is_str(str_slice(hello, 99, 5), ""), "str_slice past the end");
  check(is_str(str_slice(hello, 2, 0), ""), "str_slice of length 0");
  check(is_str(str_slice(hello, -3, 2), "h\xc3\xa9"), "str_slice(-3, 2) clamps the start");
  check(is_str(str_slice(hello, 0, -1), ""), "str_slice(count -1)");
  check(is_str(str_slice(hello, 0, 99), "h\xc3\xa9llo\xe6\x97\xa5\xe6\x9c\xac"), "str_slice whole");
  check(is_str(str_slice(I(""), 0, 0), ""), "str_slice of \"\"");

  check(is_str(str_slice_bytes(hello, 1, 2), "\xc3\xa9"), "str_slice_bytes(1, 2) one 2-byte char");
  check(is_str(str_slice_bytes(hello, 6, 3), "\xe6\x97\xa5"), "str_slice_bytes(6, 3) one 3-byte char");
  check(is_str(str_slice_bytes(hello, 12, 0), ""), "str_slice_bytes at the end, length 0");
  check(is_str(str_slice_bytes(hello, 0, 0), ""), "str_slice_bytes(0, 0)");
  check(is_str(str_slice_bytes(hello, 99, 4), ""), "str_slice_bytes past the end clamps");
  check(is_str(str_slice_bytes(hello, 9, 99), "\xe6\x9c\xac"), "str_slice_bytes length clamps");
  check(is_str(str_slice_bytes(hello, 0, 12), "h\xc3\xa9llo\xe6\x97\xa5\xe6\x9c\xac"),
        "str_slice_bytes whole");
  check(is_str(str_slice_bytes(I(""), 0, 0), ""), "str_slice_bytes of \"\"");
}

static void run_lists(void) {
  static const char* const empty1[] = { "" };
  static const char* const a_b[] = { "a", "b" };
  static const char* const a_empty[] = { "a", "" };
  static const char* const two_empty[] = { "", "" };
  static const char* const cr[] = { "a\r", "b" };
  static const char* const one[] = { "no newline" };
  static const char* const mb[] = { "h\xc3\xa9llo", "\xe6\x97\xa5" };
  static const char* const blank_mid[] = { "x", "", "y" };
  check(list_is(str_split_lines(I("")), 1, empty1), "str_split_lines(\"\")");
  check(list_is(str_split_lines(I("a\nb")), 2, a_b), "str_split_lines(a\\nb)");
  check(list_is(str_split_lines(I("a\n")), 2, a_empty), "str_split_lines(trailing newline)");
  check(list_is(str_split_lines(I("\n")), 2, two_empty), "str_split_lines(\"\\n\")");
  check(list_is(str_split_lines(I("a\r\nb")), 2, cr), "str_split_lines(CRLF keeps the \\r)");
  check(list_is(str_split_lines(I("no newline")), 1, one), "str_split_lines(no newline)");
  check(list_is(str_split_lines(I("h\xc3\xa9llo\n\xe6\x97\xa5")), 2, mb), "str_split_lines(multi-byte)");
  check(list_is(str_split_lines(I("x\n\ny")), 3, blank_mid), "str_split_lines(blank middle line)");

  static const char* const abc[] = { "a", "b", "c" };
  static const char* const lone[] = { "word" };
  static const char* const mbw[] = { "h\xc3\xa9llo", "w\xc3\xb6rld" };
  check(list_is(split_words(I("")), 0, abc), "split_words(\"\")");
  check(list_is(split_words(I("   \t\r\n ")), 0, abc), "split_words(only spaces)");
  check(list_is(split_words(I(" a  b\t\nc\r ")), 3, abc), "split_words(leading, trailing, runs)");
  check(list_is(split_words(I("word")), 1, lone), "split_words(one word)");
  check(list_is(split_words(I("h\xc3\xa9llo  w\xc3\xb6rld")), 2, mbw), "split_words(multi-byte)");

  static const char* const fresh[] = { "alpha", "beta", "gamma", "delta", "epsilon", "zeta" };
  check(list_is(str_split_lines(F("alpha\nbeta\ngamma\ndelta\nepsilon\nzeta")), 6, fresh),
        "str_split_lines of an unrooted String");
  check(list_is(split_words(F("alpha beta gamma delta epsilon zeta")), 6, fresh),
        "split_words of an unrooted String");
}

static int is_err_text(long long r, const char* want) {
  return sprout_tag(r) == ERR && is_str(sprout_field(r, 0), want);
}

static void run_err(void) {
  check(is_err_text(sprout_err_string_result("analysis service: invalid response"),
                    "analysis service: invalid response"), "sprout_err_string_result");
  check(is_err_text(sprout_err_string_result(""), ""), "sprout_err_string_result(\"\")");
  /* A service command that exits at once: the request fails and its text comes back as Err. */
  setenv("SPROUT_ANALYSIS_SERVICE_CMD", "exit 0", 1);
  setenv("SPROUT_ANALYSIS_SERVICE_LOG", "off", 1);
  long long r = analysis_check_source("x");
  check(sprout_tag(r) == ERR && is_str(sprout_field(r, 0), NULL),
        "analysis_check_source with a dead service is not Err(<headered String>)");
}

int main(int argc, char** argv) {
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  if (argc == 2 && strcmp(argv[1], "stress") == 0) setenv("SPROUT_GC_STRESS", "1", 1);
  else if (argc != 1) return 99;
  sprout_register_ctor(ERR, "Err", 1, "p");
  sprout_register_ctor(OK, "Ok", 1, "p");
  sprout_register_ctor(JUST, "Just", 1, "p");
  sprout_register_ctor(NOTHING, "Nothing", 0, "");
  sprout_register_ctor(CONS, "Cons", 2, "pp");
  sprout_register_ctor(NIL, "Nil", 0, "");
  run_scalars();
  run_concat();
  run_slices();
  run_lists();
  run_err();
  if (g_failed) return 1;
  printf("str-strings%s\n", argc == 2 ? "-stress" : "");
  return 0;
}
