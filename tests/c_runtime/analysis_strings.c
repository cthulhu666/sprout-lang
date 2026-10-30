/* The analysis-service result builders hand Sprout headered Strings in well-formed vectors and
 * tuples (docs/string-header-invariant-v0.md §7 step 3).
 *
 * The builders are static, so the runtime is included and each is called with a hand-written
 * JSON response (malloc'd, as the service reader gives it; the builder frees it). Runs under
 * SPROUT_GC_HDRCHECK=1, so every String leaving through sprout_str_word is header-checked.
 * argv[1] = "stress" also sets SPROUT_GC_STRESS=1: every allocation collects, so a vector, tuple
 * or String a builder fails to root across its next allocation is freed and fails the checks.
 * Responses carry many elements, so the per-element tuples allocate while earlier ones sit only in
 * the half-filled result vector. No builder takes a String as input, so there is no F case.
 *
 * Covered: sprout_analysis_ok_string_result_from_response, ok_vec_string_result (and _from_response),
 * ok_string_vec_pair_result (via _from_response, plus a NULL label), completion_tuple_or_fail,
 * ok_inventory_from_response (and ok_inventory_result), diagnostics_vec_or_fail,
 * ok_symbol_locations_from_response; each on a populated, an empty and a missing-key response.
 * Not covered: the sprout_builtin_fail_detail aborts of completion_tuple_or_fail and
 * diagnostics_vec_or_fail (sprout_fail), and out-of-memory branches.
 */
#include <stdio.h>
#include <stdlib.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"
#include "str_check.h"

enum { ERR = 1, OK = 2, VEC = 3 };
enum { N = 40 };

/* A malloc'd copy of a literal response. */
static char* resp(const char* text) {
  char* out = strdup(text);
  if (out == NULL) abort();
  return out;
}

/* Appends `"key":["<prefix>0",...,"<prefix>n-1"]` to buf at *len, with a leading comma if
 * *len > 0. */
static void add_strs(char* buf, size_t* len, const char* key, const char* prefix, int n) {
  if (*len > 0) buf[(*len)++] = ',';
  *len += (size_t)sprintf(buf + *len, "\"%s\":[", key);
  for (int i = 0; i < n; i++) *len += (size_t)sprintf(buf + *len, "%s\"%s%d\"", i ? "," : "", prefix, i);
  buf[(*len)++] = ']';
  buf[*len] = '\0';
}

/* Appends `"key":[base,base+1,...]` (n integers, base + i * step). */
static void add_ints(char* buf, size_t* len, const char* key, int base, int step, int n) {
  if (*len > 0) buf[(*len)++] = ',';
  *len += (size_t)sprintf(buf + *len, "\"%s\":[", key);
  for (int i = 0; i < n; i++) *len += (size_t)sprintf(buf + *len, "%s%d", i ? "," : "", base + i * step);
  buf[(*len)++] = ']';
  buf[*len] = '\0';
}

/* A response object around the fields in `buf`; the result is malloc'd. */
static char* wrap(const char* fields) {
  char* out = malloc(strlen(fields) + 3);
  if (out == NULL) abort();
  sprintf(out, "{%s}", fields);
  return out;
}

static int is_err_text(long long r, const char* want) {
  return sprout_tag(r) == ERR && is_str(sprout_field(r, 0), want);
}

/* `word` is a VectorVal of `n` headered Strings "<prefix><i>". */
static int strs_are(long long word, const char* prefix, int n) {
  if (word == 0 || sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)word)) != SPROUT_HEAP_VECTOR)
    return 0;
  const VectorVal* v = (const VectorVal*)(uintptr_t)word;
  if (v->len != n) return 0;
  for (int i = 0; i < n; i++) {
    char want[32];
    snprintf(want, sizeof want, "%s%d", prefix, i);
    if (!is_str(v->data[i], want)) return 0;
  }
  return 1;
}

/* `h` is Vec(<VectorVal of n Strings "<prefix><i>">). */
static int vec_strs_are(long long h, const char* prefix, int n) {
  return sprout_tag(h) == VEC && strs_are(sprout_field(h, 0), prefix, n);
}

static const uintptr_t* tuple_of(long long word, size_t nwords) {
  if (word == 0 || sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)word)) != SPROUT_HEAP_TUPLE)
    return NULL;
  if (sprout_hdr_aux(sprout_hdr_of((void*)(uintptr_t)word)) != nwords) return NULL;
  return (const uintptr_t*)(uintptr_t)word;
}

static void run_string_results(void) {
  long long r = sprout_analysis_ok_string_result_from_response(
      resp("{\"ok\":true,\"value\":\"he said \\\"hi\\\"\"}"), "value");
  check(sprout_tag(r) == OK && is_str(sprout_field(r, 0), "he said \"hi\""),
        "ok_string_result_from_response");
  r = sprout_analysis_ok_string_result_from_response(resp("{\"ok\":true,\"value\":\"\"}"), "value");
  check(sprout_tag(r) == OK && sprout_field(r, 0) != 0 && is_str(sprout_field(r, 0), ""),
        "ok_string_result_from_response(\"\")");
  r = sprout_analysis_ok_string_result_from_response(resp("{\"ok\":true}"), "value");
  check(is_err_text(r, "analysis service: invalid response"),
        "ok_string_result_from_response without the key is Err");

  char buf[4096];
  size_t len = 0;
  buf[0] = '\0';
  add_strs(buf, &len, "value", "item", N);
  r = sprout_analysis_ok_vec_string_result_from_response(wrap(buf), "value");
  check(sprout_tag(r) == OK && vec_strs_are(sprout_field(r, 0), "item", N),
        "ok_vec_string_result_from_response");
  r = sprout_analysis_ok_vec_string_result_from_response(resp("{\"value\":[]}"), "value");
  check(sprout_tag(r) == OK && vec_strs_are(sprout_field(r, 0), "item", 0),
        "ok_vec_string_result_from_response([])");
  r = sprout_analysis_ok_vec_string_result_from_response(resp("{\"other\":[\"a\"]}"), "value");
  check(is_err_text(r, "analysis service: invalid response"),
        "ok_vec_string_result_from_response without the key is Err");
  check(is_err_text(sprout_analysis_ok_vec_string_result(NULL), "analysis service: invalid response"),
        "ok_vec_string_result(NULL) is Err");
}

static void run_pair(void) {
  char buf[4096];
  size_t len = 0;
  buf[0] = '\0';
  len += (size_t)sprintf(buf, "\"query_type\":\"Int -> Int\"");
  add_strs(buf, &len, "matches", "m", N);
  long long r = sprout_analysis_ok_string_vec_pair_from_response(wrap(buf), "query_type", "matches");
  const uintptr_t* t = sprout_tag(r) == OK ? tuple_of(sprout_field(r, 0), 2) : NULL;
  check(t != NULL && is_str((long long)t[0], "Int -> Int") && vec_strs_are((long long)t[1], "m", N),
        "ok_string_vec_pair_from_response");

  r = sprout_analysis_ok_string_vec_pair_from_response(
      resp("{\"query_type\":\"T\",\"matches\":[]}"), "query_type", "matches");
  t = sprout_tag(r) == OK ? tuple_of(sprout_field(r, 0), 2) : NULL;
  check(t != NULL && is_str((long long)t[0], "T") && vec_strs_are((long long)t[1], "m", 0),
        "ok_string_vec_pair_from_response (no matches)");

  r = sprout_analysis_ok_string_vec_pair_from_response(
      resp("{\"matches\":[\"a\"]}"), "query_type", "matches");
  check(is_err_text(r, "analysis service: invalid response"), "pair without the label is Err");
  r = sprout_analysis_ok_string_vec_pair_from_response(
      resp("{\"query_type\":\"T\"}"), "query_type", "matches");
  check(is_err_text(r, "analysis service: invalid response"), "pair without the array is Err");
}

static void run_completion(void) {
  char buf[4096];
  size_t len = 0;
  buf[0] = '\0';
  len += (size_t)sprintf(buf, "\"prefix\":\"fo\"");
  add_strs(buf, &len, "matches", "foo", N);
  long long r = sprout_analysis_completion_tuple_or_fail("t", wrap(buf), "prefix", "matches");
  const uintptr_t* t = tuple_of(r, 2);
  check(t != NULL && is_str((long long)t[0], "fo") && vec_strs_are((long long)t[1], "foo", N),
        "completion_tuple_or_fail");
  r = sprout_analysis_completion_tuple_or_fail(
      "t", resp("{\"prefix\":\"\",\"matches\":[]}"), "prefix", "matches");
  t = tuple_of(r, 2);
  check(t != NULL && is_str((long long)t[0], "") && vec_strs_are((long long)t[1], "foo", 0),
        "completion_tuple_or_fail (empty)");
}

static void run_inventory(void) {
  char buf[8192];
  size_t len = 0;
  buf[0] = '\0';
  add_strs(buf, &len, "declared", "d", N);
  add_strs(buf, &len, "imported", "i", 0);
  add_strs(buf, &len, "exported", "e", N / 2);
  long long r = sprout_analysis_ok_inventory_from_response(wrap(buf), "declared", "imported", "exported");
  const uintptr_t* t = sprout_tag(r) == OK ? tuple_of(sprout_field(r, 0), 3) : NULL;
  check(t != NULL && vec_strs_are((long long)t[0], "d", N) && vec_strs_are((long long)t[1], "i", 0) &&
        vec_strs_are((long long)t[2], "e", N / 2), "ok_inventory_from_response");

  r = sprout_analysis_ok_inventory_from_response(
      resp("{\"declared\":[\"a\"],\"exported\":[]}"), "declared", "imported", "exported");
  check(is_err_text(r, "analysis service: invalid response"), "inventory without a key is Err");
  check(is_err_text(sprout_analysis_ok_inventory_result(NULL, NULL, NULL),
                    "analysis service: invalid response"), "ok_inventory_result(NULL) is Err");
}

static void run_diagnostics(void) {
  char buf[8192];
  size_t len = 0;
  buf[0] = '\0';
  add_strs(buf, &len, "messages", "msg", N);
  add_ints(buf, &len, "lines", 1, 1, N);
  add_ints(buf, &len, "columns", 100, 3, N);
  long long r = sprout_analysis_diagnostics_vec_or_fail("t", wrap(buf), "messages", "lines", "columns");
  int ok = sprout_tag(r) == VEC;
  const VectorVal* v = ok ? (const VectorVal*)(uintptr_t)sprout_field(r, 0) : NULL;
  ok = ok && v != NULL && v->len == N;
  for (int i = 0; ok && i < N; i++) {
    char want[32];
    snprintf(want, sizeof want, "msg%d", i);
    const uintptr_t* t = tuple_of(v->data[i], 3);
    ok = t != NULL && is_str((long long)t[0], want) && (long long)t[1] == 1 + i &&
         (long long)t[2] == 100 + 3 * i;
  }
  check(ok, "diagnostics_vec_or_fail");

  r = sprout_analysis_diagnostics_vec_or_fail(
      "t", resp("{\"messages\":[],\"lines\":[],\"columns\":[]}"), "messages", "lines", "columns");
  v = sprout_tag(r) == VEC ? (const VectorVal*)(uintptr_t)sprout_field(r, 0) : NULL;
  check(v != NULL && v->len == 0, "diagnostics_vec_or_fail (empty)");
}

static void run_symbols(void) {
  char buf[8192];
  size_t len = 0;
  buf[0] = '\0';
  add_strs(buf, &len, "categories", "cat", N);
  add_strs(buf, &len, "names", "name", N);
  add_ints(buf, &len, "lines", 10, 2, N);
  add_ints(buf, &len, "columns", 0, 5, N);
  long long r = sprout_analysis_ok_symbol_locations_from_response(
      wrap(buf), "categories", "names", "lines", "columns");
  int ok = sprout_tag(r) == OK && sprout_tag(sprout_field(r, 0)) == VEC;
  const VectorVal* v = ok ? (const VectorVal*)(uintptr_t)sprout_field(sprout_field(r, 0), 0) : NULL;
  ok = ok && v != NULL && v->len == N;
  for (int i = 0; ok && i < N; i++) {
    char cat[32], name[32];
    snprintf(cat, sizeof cat, "cat%d", i);
    snprintf(name, sizeof name, "name%d", i);
    const uintptr_t* t = tuple_of(v->data[i], 4);
    ok = t != NULL && is_str((long long)t[0], cat) && is_str((long long)t[1], name) &&
         (long long)t[2] == 10 + 2 * i && (long long)t[3] == 5 * i;
  }
  check(ok, "ok_symbol_locations_from_response");

  r = sprout_analysis_ok_symbol_locations_from_response(
      resp("{\"categories\":[],\"names\":[],\"lines\":[],\"columns\":[]}"),
      "categories", "names", "lines", "columns");
  v = sprout_tag(r) == OK ? (const VectorVal*)(uintptr_t)sprout_field(sprout_field(r, 0), 0) : NULL;
  check(v != NULL && v->len == 0, "ok_symbol_locations_from_response (empty)");

  r = sprout_analysis_ok_symbol_locations_from_response(
      resp("{\"categories\":[\"a\"],\"names\":[\"b\",\"c\"],\"lines\":[1],\"columns\":[1]}"),
      "categories", "names", "lines", "columns");
  check(is_err_text(r, "analysis service: invalid response"),
        "symbol_locations with mismatched lengths is Err");
}

int main(int argc, char** argv) {
  setenv("SPROUT_GC_HDRCHECK", "1", 1);
  setenv("SPROUT_GC_LINEAGE", "0", 1);
  if (argc == 2 && strcmp(argv[1], "stress") == 0) setenv("SPROUT_GC_STRESS", "1", 1);
  else if (argc != 1) return 99;
  sprout_register_ctor(ERR, "Err", 1, "p");
  sprout_register_ctor(OK, "Ok", 1, "p");
  sprout_register_ctor(VEC, "Vec", 1, "p");
  run_string_results();
  run_pair();
  run_completion();
  run_inventory();
  run_diagnostics();
  run_symbols();
  if (g_failed) return 1;
  printf("analysis%s\n", argc == 2 ? "-stress" : "");
  return 0;
}
