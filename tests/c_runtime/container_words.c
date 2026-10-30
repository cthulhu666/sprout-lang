/* The container builtins and GC internals hand Sprout well-formed pointer words
 * (docs/string-header-invariant-v0.md §7 step 3).
 *
 * Runs under SPROUT_GC_HDRCHECK=1, so every String that enters or leaves through the header-checked
 * paths is checked, and the sweep walk verifies slot sizes. The runtime is included, not linked, so
 * main can setenv before the first HDRCHECK query and the test can reach the static bst_* helpers.
 * argv[1] = "stress" also sets SPROUT_GC_STRESS=1: every allocation collects, so an input or a
 * node a function fails to root across its own allocations is freed and fails the byte check.
 * Map keys and vector elements are unrooted Strings (F): only the map or vector holds them.
 *
 * Covered: vector_empty, vector_append, vector_set, vector_concat, vec_make_filled,
 * vector_from_list; bst_insert_node and bst_remove_node (through map_set / map_remove, on
 * ascending, descending, zigzag and shuffled keys, checking the AVL invariants), bst_remove_min,
 * bst_rotate_left, bst_rotate_right and bst_balance (all four cases, directly);
 * sprout_alloc_closure, sprout_alloc_tuple_blob, ref_new;
 * sprout_heap_child_value_payload for every heap kind; sprout_gc_sweep (garbage is reclaimed, and
 * a lineage sweep records the corpse's backtrace in its payload).
 * Not covered: the size/arity aborts in sprout_alloc_closure and sprout_alloc_tuple_blob, the
 * null-payload and out-of-range aborts in sprout_heap_child_value_payload, and out-of-memory.
 */
#include <stdio.h>

#include "../../runtime/sprout_runtime.c"
#include "runtime_case.h"
#include "str_check.h"

enum { ERR = 1, OK = 2, JUST = 3, NOTHING = 4, CONS = 5, NIL = 6 };

#define N 127 /* prime, so i * 37 and i * 53 mod N are permutations of 0..N-1 */

static void key_text(int i, char out[16]) { snprintf(out, 16, "k%04d", i); }

/* A key nothing else roots. */
static long long K(int i) {
  char buf[16];
  key_text(i, buf);
  return F(buf);
}

static const BSTNode* node_of(long long h) { return (const BSTNode*)(uintptr_t)h; }

/* The AVL invariants of the subtree at h: keys strictly between lo and hi, heights and sizes as
 * stored, balance factor in [-1, 1]. Returns the height, or -1 on a violation. */
static int avl_height(long long h, const char* lo, const char* hi) {
  if (h == 0) return 0;
  const BSTNode* n = node_of(h);
  const char* k = n->key.hdr_p;
  if (sprout_str_headerless(k)) return -1;
  if ((lo && strcmp(lo, k) >= 0) || (hi && strcmp(k, hi) >= 0)) return -1;
  int lh = avl_height(n->left, lo, k), rh = avl_height(n->right, k, hi);
  if (lh < 0 || rh < 0 || lh - rh > 1 || rh - lh > 1) return -1;
  int height = 1 + (lh > rh ? lh : rh);
  int size = 1 + (n->left ? node_of(n->left)->size : 0) + (n->right ? node_of(n->right)->size : 0);
  return n->height == height && n->size == size ? height : -1;
}

/* The map holds exactly the keys 0..N-1 for which keep(i), in order, balanced. */
static int map_is(long long m, int (*keep)(int), const char* what) {
  int want = 0;
  for (int i = 0; i < N; i++) want += keep(i) != 0;
  if (map_size(m) != want || avl_height(m, NULL, NULL) < 0) {
    fprintf(stderr, "FAIL: %s: size %lld (want %d) or AVL invariant\n", what, map_size(m), want);
    return 0;
  }
  int at = 0;
  for (int i = 0; i < N; i++) {
    if (!keep(i)) continue;
    char buf[16];
    key_text(i, buf);
    long long just = map_nth_key(m, at++);
    if (sprout_tag(just) != JUST || !is_str(sprout_field(just, 0), buf)) {
      fprintf(stderr, "FAIL: %s: key %d\n", what, i);
      return 0;
    }
  }
  return 1;
}

static int keep_all(int i) { (void)i; return 1; }
static int keep_none(int i) { (void)i; return 0; }
static int g_drop_to;
static int keep_after(int i) { return i >= g_drop_to; }

/* Insert the keys in the order index(0..N-1), each key an unrooted String. */
static long long build(int (*index)(int)) {
  SPROUT_HANDLE(h_map, map_empty());
  for (int i = 0; i < N; i++)
    SPROUT_HANDLE_SET(h_map, map_set(sprout_handle_get(h_map), K(index(i)), 1000 + index(i)));
  return sprout_handle_get(h_map);
}
static int ascending(int i) { return i; }
static int descending(int i) { return N - 1 - i; }
static int zigzag(int i) { return i % 2 == 0 ? i / 2 : N - 1 - i / 2; }
static int shuffled(int i) { return i * 37 % N; }
static int other_shuffle(int i) { return i * 53 % N; }

static void run_map(void) {
  struct { int (*order)(int); const char* name; } cases[] = {
    { ascending, "ascending" }, { descending, "descending" },
    { zigzag, "zigzag" }, { shuffled, "shuffled" },
  };
  for (int c = 0; c < 4; c++) {
    SPROUT_HANDLE(h_map, build(cases[c].order));
    long long m = sprout_handle_get(h_map);
    check(map_is(m, keep_all, cases[c].name), cases[c].name);
    long long found = map_get(m, K(77));
    check(sprout_tag(found) == JUST && sprout_field(found, 0) == 1077, "map_get after insert");

    /* Overwrite: same keys, new values, same shape. */
    SPROUT_HANDLE(h_over, m);
    for (int i = 0; i < N; i += 9)
      SPROUT_HANDLE_SET(h_over, map_set(sprout_handle_get(h_over), K(i), 5));
    check(map_is(sprout_handle_get(h_over), keep_all, "overwrite"), "map_set overwrite");
    check(map_is(m, keep_all, "persistent"), "map_set leaves the old map intact");

    /* Remove every key in a different order; check the invariants as the map shrinks. */
    SPROUT_HANDLE(h_rm, m);
    int removed = 0;
    for (int i = 0; i < N; i++) {
      int k = other_shuffle(i);
      SPROUT_HANDLE_SET(h_rm, map_remove(sprout_handle_get(h_rm), K(k)));
      removed++;
      if (avl_height(sprout_handle_get(h_rm), NULL, NULL) < 0 ||
          map_size(sprout_handle_get(h_rm)) != N - removed) {
        check(0, "map_remove keeps the AVL invariants");
        break;
      }
      if (sprout_tag(map_get(sprout_handle_get(h_rm), K(k))) != NOTHING) {
        check(0, "map_remove drops the key");
        break;
      }
    }
    check(map_is(sprout_handle_get(h_rm), keep_none, "emptied"), "map emptied by map_remove");
    check(map_is(m, keep_all, "persistent"), "map_remove leaves the old map intact");
  }

  /* An absent key changes nothing. */
  SPROUT_HANDLE(h_one, map_set(map_empty(), K(3), 1));
  check(map_remove(sprout_handle_get(h_one), K(4)) == sprout_handle_get(h_one),
        "map_remove of an absent key returns the map itself");

  /* bst_remove_min drops the smallest key and leaves the original alone. */
  SPROUT_HANDLE(h_full, build(shuffled));
  long long smaller = bst_remove_min(sprout_handle_get(h_full));
  g_drop_to = 1;
  check(map_is(smaller, keep_after, "remove_min"), "bst_remove_min drops key 0");
  check(map_is(sprout_handle_get(h_full), keep_all, "remove_min source"),
        "bst_remove_min leaves the old map intact");
  check(bst_remove_min(0) == 0, "bst_remove_min of the empty map");
}

/* A node with the given children, its key a String only the node holds. */
static long long mk_node(int key, long long left, long long right) {
  SPROUT_HANDLE(h_l, left);
  SPROUT_HANDLE(h_r, right);
  SPROUT_HANDLE(h_k, K(key));
  BSTNode* n = sprout_alloc_bst_node("container_words: node");
  n->key = sprout_str_of_word(sprout_handle_get(h_k));
  n->value = key;
  n->left = sprout_handle_get(h_l);
  n->right = sprout_handle_get(h_r);
  bst_update(n);
  return sprout_ptr_word(n);
}

static int has_key(long long h, int key) {
  char buf[16];
  key_text(key, buf);
  return h != 0 && strcmp(node_of(h)->key.hdr_p, buf) == 0;
}

/* The tree at h is root with leaves left and right, all keys intact and balanced. */
static int shape_is(long long h, int root, int left, int right) {
  return has_key(h, root) && has_key(node_of(h)->left, left) && has_key(node_of(h)->right, right) &&
         avl_height(h, NULL, NULL) == 2 && node_of(h)->size == 3;
}

static void run_rotations(void) {
  /* Keys 0 < 1 < 2. Right chain 0 -> 1 -> 2. */
  SPROUT_HANDLE(h_rr, mk_node(0, 0, mk_node(1, 0, mk_node(2, 0, 0))));
  check(shape_is(bst_rotate_left(sprout_handle_get(h_rr)), 1, 0, 2), "bst_rotate_left");
  check(shape_is(bst_balance(sprout_handle_get(h_rr)), 1, 0, 2), "bst_balance, right-right");
  /* Left chain 2 -> 1 -> 0. */
  SPROUT_HANDLE(h_ll, mk_node(2, mk_node(1, mk_node(0, 0, 0), 0), 0));
  check(shape_is(bst_rotate_right(sprout_handle_get(h_ll)), 1, 0, 2), "bst_rotate_right");
  check(shape_is(bst_balance(sprout_handle_get(h_ll)), 1, 0, 2), "bst_balance, left-left");
  /* Left-right: 2 with left child 0 whose right child is 1. */
  SPROUT_HANDLE(h_lr, mk_node(2, mk_node(0, 0, mk_node(1, 0, 0)), 0));
  check(shape_is(bst_balance(sprout_handle_get(h_lr)), 1, 0, 2), "bst_balance, left-right");
  /* Right-left: 0 with right child 2 whose left child is 1. */
  SPROUT_HANDLE(h_rl, mk_node(0, 0, mk_node(2, mk_node(1, 0, 0), 0)));
  check(shape_is(bst_balance(sprout_handle_get(h_rl)), 1, 0, 2), "bst_balance, right-left");
  /* The sources are untouched (structural sharing). */
  check(has_key(sprout_handle_get(h_rr), 0) && node_of(sprout_handle_get(h_rr))->size == 3,
        "rotation leaves its source intact");
  check(bst_balance(0) == 0, "bst_balance of the empty map");
}

static int is_vec(long long v, int n) {
  return v != 0 && sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)v)) == SPROUT_HEAP_VECTOR &&
         vector_length(v) == n;
}

/* Element i of the vector is the String "e<i>". */
static int elem_is(long long v, int i) {
  char buf[16];
  snprintf(buf, sizeof buf, "e%d", i);
  long long got = vector_get(v, i);
  return sprout_tag(got) == JUST && is_str(sprout_field(got, 0), buf);
}

static long long E(int i) {
  char buf[16];
  snprintf(buf, sizeof buf, "e%d", i);
  return F(buf);
}

static void run_vectors(void) {
  check(is_vec(vector_empty(), 0), "vector_empty");

  /* Elements are unrooted Strings; once stored only the vector holds them. */
  SPROUT_HANDLE(h_v, vector_empty());
  for (int i = 0; i < 40; i++)
    SPROUT_HANDLE_SET(h_v, vector_append(sprout_handle_get(h_v), E(i)));
  long long v = sprout_handle_get(h_v);
  int ok = is_vec(v, 40);
  for (int i = 0; ok && i < 40; i++) ok = elem_is(v, i);
  check(ok, "vector_append");

  SPROUT_HANDLE(h_set, vector_set(v, 7, F("replaced")));
  long long s = sprout_handle_get(h_set);
  long long got = vector_get(s, 7);
  check(is_vec(s, 40) && sprout_tag(got) == JUST && is_str(sprout_field(got, 0), "replaced"),
        "vector_set replaces the element");
  check(elem_is(s, 6) && elem_is(s, 8) && elem_is(v, 7), "vector_set copies and leaves the source");
  check(is_vec(vector_set(v, 99, F("out of range")), 40) && elem_is(v, 39),
        "vector_set out of range keeps the vector");
  SPROUT_HANDLE(h_none, vector_empty());
  check(is_vec(vector_set(sprout_handle_get(h_none), 0, F("x")), 0), "vector_set on the empty vector");

  SPROUT_HANDLE(h_w, vector_empty());
  for (int i = 40; i < 90; i++)
    SPROUT_HANDLE_SET(h_w, vector_append(sprout_handle_get(h_w), E(i)));
  SPROUT_HANDLE(h_cat, vector_concat(v, sprout_handle_get(h_w)));
  long long c = sprout_handle_get(h_cat);
  ok = is_vec(c, 90);
  for (int i = 0; ok && i < 90; i++) ok = elem_is(c, i);
  check(ok, "vector_concat");
  check(is_vec(vector_concat(sprout_handle_get(h_none), sprout_handle_get(h_none)), 0),
        "vector_concat of two empty vectors");
  check(is_vec(vector_concat(c, sprout_handle_get(h_none)), 90) && elem_is(c, 89),
        "vector_concat with an empty vector");

  /* The fill value is an unrooted String. */
  SPROUT_HANDLE(h_fill, vec_make_filled(60, F("fill")));
  long long f = sprout_handle_get(h_fill);
  ok = is_vec(f, 60);
  for (int i = 0; ok && i < 60; i++) {
    long long g = vector_get(f, i);
    ok = sprout_tag(g) == JUST && is_str(sprout_field(g, 0), "fill");
  }
  check(ok, "vec_make_filled");
  check(is_vec(vec_make_filled(0, 0), 0), "vec_make_filled(0)");

  /* A list of unrooted Strings; once the vector exists nothing else holds them. */
  SPROUT_HANDLE(h_list, sprout_make0(NIL));
  for (int i = 49; i >= 0; i--) {
    SPROUT_HANDLE(h_e, E(i)); /* sprout_make2 does not root its arguments */
    SPROUT_HANDLE_SET(h_list,
                      sprout_make2(CONS, sprout_handle_get(h_e), sprout_handle_get(h_list)));
  }
  SPROUT_HANDLE(h_fl, vector_from_list(sprout_handle_get(h_list)));
  SPROUT_HANDLE_SET(h_list, 0);
  long long fl = sprout_handle_get(h_fl);
  ok = is_vec(fl, 50);
  for (int i = 0; ok && i < 50; i++) ok = elem_is(fl, i);
  check(ok, "vector_from_list");
  check(is_vec(vector_from_list(sprout_make0(NIL)), 0), "vector_from_list of Nil");
}

/* The words every heap kind exposes as GC children. */
static void run_child_values(void) {
  SPROUT_HANDLE(h_obj, sprout_make2(CONS, 11, 22));
  long long obj = sprout_handle_get(h_obj);
  check(sprout_heap_child_value_payload((void*)(uintptr_t)obj, 0) == 11 &&
        sprout_heap_child_value_payload((void*)(uintptr_t)obj, 1) == 22, "child values of an object");

  SPROUT_HANDLE(h_vec, vec_make_filled(3, 33));
  long long vec = sprout_handle_get(h_vec);
  check(sprout_heap_child_value_payload((void*)(uintptr_t)vec, 2) == 33, "child value of a vector");

  SPROUT_HANDLE(h_ref, ref_new(44));
  long long ref = sprout_handle_get(h_ref);
  check(sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)ref)) == SPROUT_HEAP_REF,
        "ref_new returns a REF block");
  check(sprout_heap_child_value_payload((void*)(uintptr_t)ref, 0) == 44, "child value of a ref");

  /* A map node: value, left, right, then the key as a headered String word. */
  SPROUT_HANDLE(h_left, mk_node(0, 0, 0));
  SPROUT_HANDLE(h_right, mk_node(2, 0, 0));
  SPROUT_HANDLE(h_node, mk_node(1, sprout_handle_get(h_left), sprout_handle_get(h_right)));
  void* node = (void*)(uintptr_t)sprout_handle_get(h_node);
  check(sprout_heap_child_value_payload(node, 0) == 1 &&
        sprout_heap_child_value_payload(node, 1) == sprout_handle_get(h_left) &&
        sprout_heap_child_value_payload(node, 2) == sprout_handle_get(h_right), "child values of a map node");
  check(is_str(sprout_heap_child_value_payload(node, 3), "k0001"), "map node key child is a String");

  /* A builder: its chunks are Bytes blocks. */
  SPROUT_HANDLE(h_b, bytes_builder_u32_be(0x01020304));
  void* b = (void*)(uintptr_t)sprout_handle_get(h_b);
  long long chunk = sprout_heap_child_value_payload(b, 0);
  check(chunk == sprout_ptr_word(((BuilderVal*)b)->chunks[0]) && chunk != 0 &&
        sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)chunk)) == SPROUT_HEAP_BYTES,
        "child value of a builder is its Bytes chunk");

  /* A closure: slot 0 is the code pointer and is skipped. */
  SPROUT_HANDLE(h_cl, sprout_alloc_closure(3 * 8, 1));
  long long* cl = (long long*)(uintptr_t)sprout_handle_get(h_cl);
  cl[1] = 55; cl[2] = 66;
  check(sprout_heap_child_value_payload(cl, 0) == 55 && sprout_heap_child_value_payload(cl, 1) == 66,
        "child values of a closure");

  /* A tuple blob: raw words. */
  SPROUT_HANDLE(h_t, sprout_alloc_tuple_blob(2 * 8));
  long long* t = (long long*)(uintptr_t)sprout_handle_get(h_t);
  t[0] = 77; t[1] = 88;
  check(sprout_heap_child_value_payload(t, 0) == 77 && sprout_heap_child_value_payload(t, 1) == 88,
        "child values of a tuple");
}

static void run_blocks(void) {
  SPROUT_HANDLE(h_c, sprout_alloc_closure(4 * 8, 2));
  void* c = (void*)(uintptr_t)sprout_handle_get(h_c);
  uint64_t hc = sprout_hdr_of(c);
  check(sprout_hdr_kind(hc) == SPROUT_HEAP_CLOSURE && sprout_closure_ncaps(sprout_hdr_aux(hc)) == 3,
        "sprout_alloc_closure: a CLOSURE block with 3 captures");
  int zeroed = 1;
  for (int i = 0; i < 4; i++) zeroed = zeroed && ((long long*)c)[i] == 0;
  check(zeroed, "sprout_alloc_closure zero-fills the environment");

  SPROUT_HANDLE(h_c0, sprout_alloc_closure(0, 0));
  check(sprout_hdr_kind(sprout_hdr_of((void*)(uintptr_t)sprout_handle_get(h_c0))) == SPROUT_HEAP_CLOSURE,
        "sprout_alloc_closure of size 0");

  SPROUT_HANDLE(h_t, sprout_alloc_tuple_blob(5 * 8));
  uint64_t ht = sprout_hdr_of((void*)(uintptr_t)sprout_handle_get(h_t));
  check(sprout_hdr_kind(ht) == SPROUT_HEAP_TUPLE && sprout_hdr_aux(ht) == 5,
        "sprout_alloc_tuple_blob: a TUPLE block of 5 words");
}

/* Garbage is reclaimed; live data is kept. */
static void run_sweep(void) {
  sprout_gc_collect_with_reason("container_words");
  SPROUT_HANDLE(h_keep, vec_make_filled(4, 9));
  sprout_gc_collect_with_reason("container_words");
  long long base = g_managed_heap_count;
  for (int i = 0; i < 100; i++) (void)vector_empty();
  sprout_gc_collect_with_reason("container_words");
  check(g_managed_heap_count <= base, "the sweep reclaims unreachable vectors");
  check(is_vec(sprout_handle_get(h_keep), 4), "the sweep keeps a rooted vector");

  /* Under lineage a swept OBJ becomes a corpse whose payload holds the free backtrace. */
  g_gc_lineage = 1;
  long long* corpse = (long long*)(uintptr_t)sprout_make2(CONS, 1, 2);
  sprout_gc_collect_with_reason("container_words");
  g_gc_lineage = 0;
  check(sprout_hdr_kind(sprout_hdr_of(corpse)) == (SproutHeapKind)SPROUT_GC_POISON &&
        corpse[0] != 0 && corpse[1] > 0, "a lineage sweep stores the backtrace in the corpse");
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
  run_vectors();
  run_blocks();
  run_child_values();
  run_rotations();
  run_map();
  run_sweep();
  if (g_failed) return 1;
  printf("containers%s\n", argc == 2 ? "-stress" : "");
  return 0;
}
