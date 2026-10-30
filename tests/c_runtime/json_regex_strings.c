/* The json and regex builtins take headered Strings and hand back headered Strings
 * (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that leaves through sprout_str_word or enters
 * through sprout_str_of_word is header-checked, and str_eq / str_byte_len check each payload again.
 * The runtime is included, not linked, so main can setenv before the first HDRCHECK query.
 * argv[1] = "stress" also sets SPROUT_GC_STRESS=1: every allocation collects, so an input a
 * function fails to root across its own allocations is freed and fails the byte check. Inputs
 * are unrooted Strings (F).
 *
 * Covered: json_stringify (JsonFloat, control-character escapes, an unrooted top node),
 * regex_escape, regex_replace_all_literal (including an empty match and empty text),
 * regex_validate (Ok and each Err text), sprout_json_extract_string_array and
 * sprout_json_encode_string_array_from_vec_handle.
 * Not covered: the null-input and wrong-shape aborts (sprout_fail), regex_replace_all_literal
 * with an invalid pattern (it aborts), and out-of-memory branches.
 */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"
#include "str_check.h"

enum {
  ERR = 1, OK, VEC, J_ARRAY, J_ARRAY_CONS, J_ARRAY_NIL, J_BOOL, J_FLOAT, J_INT, J_NULL,
  J_OBJECT, J_OBJECT_CONS, J_OBJECT_NIL, J_STRING
};

/* Appends printf output to a malloc'd buffer. */
typedef struct { char* data; size_t len; } Text;
static void put(Text* t, const char* fmt, ...) __attribute__((format(printf, 2, 3)));
static void put(Text* t, const char* fmt, ...) {
  char piece[256];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(piece, sizeof(piece), fmt, ap);
  va_end(ap);
  t->data = realloc(t->data, t->len + (size_t)n + 1);
  memcpy(t->data + t->len, piece, (size_t)n + 1);
  t->len += (size_t)n;
}

/* A boxed Double. */
static long long J_FLT(double d) {
  long long bits;
  memcpy(&bits, &d, sizeof(bits));
  return sprout_make1(J_FLOAT, bits);
}

static void run_stringify(void) {
  /* {"k\x01\"": 1.5, "n": 2.0, "s": "tab\t"} built by hand, every node rooted by the test */
  SPROUT_HANDLE(h_k1, F("k\x01\""));
  SPROUT_HANDLE(h_n, F("n"));
  SPROUT_HANDLE(h_s, F("s"));
  SPROUT_HANDLE(h_tab, F("tab\t"));
  SPROUT_HANDLE(h_nil, sprout_make0(J_OBJECT_NIL));
  SPROUT_HANDLE(h_vs, sprout_make1(J_STRING, sprout_handle_get(h_tab)));
  SPROUT_HANDLE(h_c3, sprout_make3(J_OBJECT_CONS, sprout_handle_get(h_s), sprout_handle_get(h_vs),
                                   sprout_handle_get(h_nil)));
  SPROUT_HANDLE(h_v2, J_FLT(2.0));
  SPROUT_HANDLE(h_c2, sprout_make3(J_OBJECT_CONS, sprout_handle_get(h_n), sprout_handle_get(h_v2),
                                   sprout_handle_get(h_c3)));
  SPROUT_HANDLE(h_v1, J_FLT(1.5));
  SPROUT_HANDLE(h_c1, sprout_make3(J_OBJECT_CONS, sprout_handle_get(h_k1), sprout_handle_get(h_v1),
                                   sprout_handle_get(h_c2)));
  long long tree = sprout_make1(J_OBJECT, sprout_handle_get(h_c1));
  check(is_str(json_stringify(tree), "{\"k\\u0001\\\"\":1.5,\"n\":2.0,\"s\":\"tab\\t\"}"),
        "json_stringify of floats and escapes");

  check(is_str(json_stringify(sprout_make0(J_NULL)), "null"), "json_stringify(null)");
  check(is_str(json_stringify(sprout_make1(J_BOOL, 0)), "false"), "json_stringify(false)");
  check(is_str(json_stringify(sprout_make1(J_INT, -7)), "-7"), "json_stringify(-7)");
  SPROUT_HANDLE(h_empty, sprout_make0(J_ARRAY_NIL));
  check(is_str(json_stringify(sprout_make1(J_ARRAY, sprout_handle_get(h_empty))), "[]"),
        "json_stringify([])");
  SPROUT_HANDLE(h_e, F(""));
  check(is_str(json_stringify(sprout_make1(J_STRING, sprout_handle_get(h_e))), "\"\""),
        "json_stringify of the empty string");
}

/* One argument at a time is an unrooted String and the others interned: C evaluates the arguments
 * in no fixed order, so a second F could free the first before the call. */
static void replaces(const char* p, const char* r, const char* t, const char* want,
                     const char* what) {
  char label[96];
  snprintf(label, sizeof(label), "regex_replace_all_literal(%s)", what);
  check(is_str(regex_replace_all_literal(F(p), I(r), I(t)), want), label);
  check(is_str(regex_replace_all_literal(I(p), F(r), I(t)), want), label);
  check(is_str(regex_replace_all_literal(I(p), I(r), F(t)), want), label);
}

static void run_regex(void) {
  check(is_str(regex_escape(F("plain")), "plain"), "regex_escape(plain)");
  check(is_str(regex_escape(F("")), ""), "regex_escape(\"\")");
  check(is_str(regex_escape(F("a.b*c(d)")), "a\\.b\\*c\\(d\\)"), "regex_escape(metacharacters)");
  check(is_str(regex_escape(F("[x]{1}|^$+?-\\")), "\\[x\\]\\{1\\}\\|\\^\\$\\+\\?\\-\\\\"),
        "regex_escape(every special)");
  check(is_str(regex_escape(F("\xc3\xa9+")), "\xc3\xa9\\+"), "regex_escape(non-ASCII)");

  replaces("[0-9]+", "#", "a12b345c", "a#b#c", "digits");
  replaces("\\d+", "<\xc3\xa9>", "x1y22", "x<\xc3\xa9>y<\xc3\xa9>", "\\d, non-ASCII replacement");
  replaces("zz", "!", "no match", "no match", "no match");
  replaces("x*", "-", "ab", "-a-b-", "empty matches");
  replaces("a", "b", "", "", "empty text");
  replaces("a", "", "banana", "bnn", "empty replacement");
  Text long_in = {0}, long_want = {0};
  for (int i = 0; i < 300; i++) { put(&long_in, "ab%d;", i); put(&long_want, "ab#;"); }
  replaces("[0-9]+", "#", long_in.data, long_want.data, "a long text");
  free(long_in.data); free(long_want.data);

  long long ok = regex_validate(F("[a-z]+(x|y)*"));
  check(sprout_tag(ok) == OK && sprout_field(ok, 0) == 0, "regex_validate(valid) is Ok(Unit)");

  static const char* const bad[][2] = {
    {"a{2}", "unsupported regex feature: counted repetition"},
    {"a\\", "invalid regex pattern: trailing escape"},
    {"\\1", "unsupported regex feature: backreferences"},
    {"\\q", "unsupported regex feature: escape \\q"},
    {"a*?", "unsupported regex feature: non-greedy quantifiers"},
    {"(?:a)", "unsupported regex feature: extended group syntax"},
  };
  for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
    long long r = regex_validate(F(bad[i][0]));
    check(sprout_tag(r) == ERR && is_str(sprout_field(r, 0), bad[i][1]), bad[i][0]);
  }
  /* regcomp's own text differs by platform; only the prefix is ours */
  long long r = regex_validate(F("(a"));
  check(sprout_tag(r) == ERR && is_str(sprout_field(r, 0), NULL) &&
        strncmp((const char*)(uintptr_t)sprout_field(r, 0), "invalid regex pattern: ", 23) == 0,
        "regex_validate(\"(a\") is Err(\"invalid regex pattern: ...\")");
}

static void run_extract_and_encode(void) {
  const char* text = "{\"x\": [\"a\", \"b\\n\", \"\\u00e9\\\"\"], \"y\": [], \"z\": [\"p\", \"q";
  VectorVal* v = sprout_json_extract_string_array(text, "x");
  check(v != NULL && v->len == 3, "extract: three items");
  if (v != NULL && v->len == 3) {
    check(is_str(v->data[0], "a") && is_str(v->data[1], "b\n") && is_str(v->data[2], "\xc3\xa9\""),
          "extract: items are headered Strings with the escapes decoded");
  }
  v = sprout_json_extract_string_array(text, "y");
  check(v != NULL && v->len == 0, "extract: empty array");
  check(sprout_json_extract_string_array(text, "missing") == NULL, "extract: missing key");
  v = sprout_json_extract_string_array(text, "z");
  check(v != NULL && v->len == 1 && is_str(v->data[0], "p"), "extract: cut array keeps what parsed");

  Text big = {0};
  put(&big, "{\"k\":[");
  for (int i = 0; i < 200; i++) put(&big, "%s\"s%d\"", i ? "," : "", i);
  put(&big, "]}");
  v = sprout_json_extract_string_array(big.data, "k");
  int all = v != NULL && v->len == 200;
  for (int i = 0; all && i < 200; i++) {
    char want[16];
    snprintf(want, sizeof(want), "s%d", i);
    all = is_str(v->data[i], want);
  }
  check(all, "extract: 200 items");
  free(big.data);

  /* encode: wrap an extracted vector as Vec and write it back as JSON */
  SPROUT_HANDLE(h_vec, sprout_ptr_word(sprout_json_extract_string_array(text, "x")));
  SPROUT_HANDLE(h_wrapped, sprout_make1(VEC, sprout_handle_get(h_vec)));
  char* json = sprout_json_encode_string_array_from_vec_handle(
      (const void*)(uintptr_t)sprout_handle_get(h_wrapped), "t", "items");
  check(strcmp(json, "[\"a\",\"b\\n\",\"\xc3\xa9\\\"\"]") == 0, "encode: escaped items");
  free(json);
  SPROUT_HANDLE(h_none, sprout_ptr_word(sprout_json_extract_string_array("{\"e\":[]}", "e")));
  SPROUT_HANDLE(h_none_vec, sprout_make1(VEC, sprout_handle_get(h_none)));
  json = sprout_json_encode_string_array_from_vec_handle(
      (const void*)(uintptr_t)sprout_handle_get(h_none_vec), "t", "items");
  check(strcmp(json, "[]") == 0, "encode: empty vec");
  free(json);
}

int main(int argc, char** argv) {
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  if (argc == 2 && strcmp(argv[1], "stress") == 0) setenv("SPROUT_GC_STRESS", "1", 1);
  else if (argc != 1) return 99;
  sprout_register_ctor(ERR, "Err", 1, "p");
  sprout_register_ctor(OK, "Ok", 1, "p");
  sprout_register_ctor(VEC, "Vec", 1, "p");
  sprout_register_ctor(J_ARRAY, "stdlib.json.JsonArray", 1, "p");
  sprout_register_ctor(J_ARRAY_CONS, "stdlib.json.JsonArrayCons", 2, "pp");
  sprout_register_ctor(J_ARRAY_NIL, "stdlib.json.JsonArrayNil", 0, "");
  sprout_register_ctor(J_BOOL, "stdlib.json.JsonBool", 1, "b");
  sprout_register_ctor(J_FLOAT, "stdlib.json.JsonFloat", 1, "i");
  sprout_register_ctor(J_INT, "stdlib.json.JsonInt", 1, "i");
  sprout_register_ctor(J_NULL, "stdlib.json.JsonNull", 0, "");
  sprout_register_ctor(J_OBJECT, "stdlib.json.JsonObject", 1, "p");
  sprout_register_ctor(J_OBJECT_CONS, "stdlib.json.JsonObjectCons", 3, "spp");
  sprout_register_ctor(J_OBJECT_NIL, "stdlib.json.JsonObjectNil", 0, "");
  sprout_register_ctor(J_STRING, "stdlib.json.JsonString", 1, "s");
  run_stringify();
  run_regex();
  run_extract_and_encode();
  if (g_failed) return 1;
  printf("json-regex%s\n", argc == 2 ? "-stress" : "");
  return 0;
}
