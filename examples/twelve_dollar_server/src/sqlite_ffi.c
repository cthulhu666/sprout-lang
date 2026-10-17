/*
 * Thin C shim between Sprout `extern fn`s and libsqlite3.
 *
 * Every parameter and return is `long long`: that is how `extern fn` lowers
 * (`declare i64 @name(i64, ...)`). A Sprout `String` arrives as a
 * NUL-terminated `const char*`. Handles (`sqlite3*`, `sqlite3_stmt*`) go back
 * to Sprout as plain Ints; the GC ignores them.
 *
 * The runtime's constructors (`sprout_make*`, `intern_string`,
 * `find_ctor_tag_by_name`) are `static` in sprout_runtime.c. The one Sprout
 * value the shim builds is a column's text as a `String`, through the
 * runtime's exported `str_concat` builtin. Other text crosses back one byte per
 * call and is assembled in Sprout. See docs/sqlite-driver-mvp.md, "FFI
 * boundary".
 *
 * Every name is prefixed `sqlite_ffi_`: an extern that collides with a runtime
 * symbol has its declare silently dropped by the compiler.
 */
#include <sqlite3.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define DB(h) ((sqlite3*)(uintptr_t)(h))
#define STMT(h) ((sqlite3_stmt*)(uintptr_t)(h))
#define STR(s) ((const char*)(uintptr_t)(s))

long long sqlite_ffi_libversion_number(void) {
  return (long long)sqlite3_libversion_number();
}

/* Returns the db handle, or 0 only when sqlite could not allocate one. On any
 * other failure the handle carries the error: read errcode/errmsg, then close
 * it. */
long long sqlite_ffi_open(long long path, long long flags) {
  sqlite3* db = NULL;
  sqlite3_open_v2(STR(path), &db, (int)flags, NULL);
  return (long long)(uintptr_t)db;
}

long long sqlite_ffi_close(long long db) {
  return (long long)sqlite3_close_v2(DB(db));
}

long long sqlite_ffi_errcode(long long db) {
  return (long long)sqlite3_extended_errcode(DB(db));
}

/* sqlite3_errmsg may return NULL; that reads as an empty message. */
long long sqlite_ffi_errmsg_len(long long db) {
  const char* msg = sqlite3_errmsg(DB(db));
  return msg == NULL ? 0 : (long long)strlen(msg);
}

long long sqlite_ffi_errmsg_byte(long long db, long long i) {
  return (long long)(unsigned char)sqlite3_errmsg(DB(db))[i];
}

/* sqlite3_changes64 is 3.37.0+; older headers get the int version. */
long long sqlite_ffi_changes(long long db) {
#if SQLITE_VERSION_NUMBER >= 3037000
  return (long long)sqlite3_changes64(DB(db));
#else
  return (long long)sqlite3_changes(DB(db));
#endif
}

long long sqlite_ffi_last_insert_rowid(long long db) {
  return (long long)sqlite3_last_insert_rowid(DB(db));
}

/* Runs every statement in `sql` and discards rows; stops at the first error.
 * Returns the result code; on error read errcode/errmsg. */
long long sqlite_ffi_exec(long long db, long long sql) {
  return (long long)sqlite3_exec(DB(db), STR(sql), NULL, NULL, NULL);
}

/* True when `s` holds only whitespace, `;`, and comments: what sqlite's
 * tokenizer skips without producing a statement. */
static int only_trivia(const char* s) {
  for (;;) {
    char c = *s;
    if (c == '\0') return 1;
    if (c == ' ' || c == '\t' || c == '\n' || c == '\f' || c == '\r' || c == ';') {
      s++;
    } else if (c == '-' && s[1] == '-') {
      while (*s != '\0' && *s != '\n') s++;
    } else if (c == '/' && s[1] == '*') {
      const char* end = strstr(s + 2, "*/");
      if (end == NULL) return 1; /* sqlite accepts an unterminated trailing comment */
      s = end + 2;
    } else {
      return 0;
    }
  }
}

/* Returned by prepare when SQL follows the first statement. Outside the
 * result-code range, so it cannot be mistaken for -rc. */
#define SQLITE_FFI_EXTRA_SQL (-(1LL << 32))

/* Prepares the single statement in `sql`. Returns the stmt handle (> 0;
 * user-space pointers fit below 2^63), 0 when `sql` holds no statement, -rc
 * when prepare fails (read errmsg), or SQLITE_FFI_EXTRA_SQL when another
 * statement follows the first. */
long long sqlite_ffi_prepare(long long db, long long sql) {
  sqlite3_stmt* stmt = NULL;
  const char* tail = NULL;
  int rc = sqlite3_prepare_v2(DB(db), STR(sql), -1, &stmt, &tail);
  if (rc != SQLITE_OK) return -(long long)rc;
  if (stmt == NULL) return 0;
  if (tail != NULL && !only_trivia(tail)) {
    sqlite3_finalize(stmt);
    return SQLITE_FFI_EXTRA_SQL;
  }
  return (long long)(uintptr_t)stmt;
}

long long sqlite_ffi_step(long long stmt) {
  return (long long)sqlite3_step(STMT(stmt));
}

long long sqlite_ffi_finalize(long long stmt) {
  return (long long)sqlite3_finalize(STMT(stmt));
}

long long sqlite_ffi_column_count(long long stmt) {
  return (long long)sqlite3_column_count(STMT(stmt));
}

/* SQLITE_INTEGER=1, FLOAT=2, TEXT=3, BLOB=4, NULL=5. */
long long sqlite_ffi_column_type(long long stmt, long long i) {
  return (long long)sqlite3_column_type(STMT(stmt), (int)i);
}

long long sqlite_ffi_column_int(long long stmt, long long i) {
  return (long long)sqlite3_column_int64(STMT(stmt), (int)i);
}

/* Text is read as `len` then `byte(k)`. Calling column_text before
 * column_bytes fixes the conversion, so the pointer stays stable across the
 * byte calls until the next step/reset/finalize. Returns -1 on out of memory
 * (column_text gives NULL and errcode is SQLITE_NOMEM). */
long long sqlite_ffi_column_text_len(long long stmt, long long i) {
  if (sqlite3_column_text(STMT(stmt), (int)i) == NULL &&
      sqlite3_errcode(sqlite3_db_handle(STMT(stmt))) == SQLITE_NOMEM) {
    return -1;
  }
  return (long long)sqlite3_column_bytes(STMT(stmt), (int)i);
}

long long sqlite_ffi_column_text_byte(long long stmt, long long i, long long k) {
  return (long long)sqlite3_column_text(STMT(stmt), (int)i)[k];
}

/* Accepts exactly what the runtime's `utf8_validate` accepts (bytes.to_string
 * goes through it): well-formed UTF-8. Rejects NUL, truncated sequences,
 * overlong forms, surrogates and code points above U+10FFFF. */
static int plain_utf8(const unsigned char* s, int n) {
  int i = 0;
  while (i < n) {
    unsigned char b0 = s[i];
    if (b0 == 0) return 0;
    if (b0 <= 0x7F) { i += 1; continue; }
    if ((b0 & 0xE0) == 0xC0) {
      if (i + 1 >= n || b0 < 0xC2 || (s[i + 1] & 0xC0) != 0x80) return 0;
      i += 2;
    } else if ((b0 & 0xF0) == 0xE0) {
      if (i + 2 >= n || (s[i + 1] & 0xC0) != 0x80 || (s[i + 2] & 0xC0) != 0x80) return 0;
      if ((b0 == 0xE0 && s[i + 1] < 0xA0) || (b0 == 0xED && s[i + 1] >= 0xA0)) return 0;
      i += 3;
    } else if ((b0 & 0xF8) == 0xF0) {
      if (i + 3 >= n || (s[i + 1] & 0xC0) != 0x80 || (s[i + 2] & 0xC0) != 0x80 ||
          (s[i + 3] & 0xC0) != 0x80) return 0;
      if ((b0 == 0xF0 && s[i + 1] < 0x90) || (b0 == 0xF4 && s[i + 1] >= 0x90) || b0 > 0xF4) return 0;
      i += 4;
    } else {
      return 0;
    }
  }
  return 1;
}

/* 1 when the column's text can be copied whole by column_text_str: sqlite
 * returned it and it is plain UTF-8. 0 sends the caller to the byte path,
 * which reports out of memory, NUL and bad UTF-8 as before. */
long long sqlite_ffi_column_text_plain(long long stmt, long long i) {
  const unsigned char* text = sqlite3_column_text(STMT(stmt), (int)i);
  if (text == NULL) return 0;
  return plain_utf8(text, sqlite3_column_bytes(STMT(stmt), (int)i));
}

/* A Sprout builtin in sprout_runtime.c: a new GC-managed String holding
 * `left` then `right`, both NUL-terminated. It may collect, which is safe:
 * the compiler roots live values around every extern call it cannot prove
 * non-allocating, and this is one. */
long long str_concat(long long left, long long right);

/* The column's text as a Sprout String, in one copy. Call only after
 * column_text_plain returned 1 for this row: then the text has no NUL, so
 * str_concat's strlen sees all of it. */
long long sqlite_ffi_column_text_str(long long stmt, long long i) {
  return str_concat((long long)(uintptr_t)"",
                    (long long)(uintptr_t)sqlite3_column_text(STMT(stmt), (int)i));
}

/* Returns -1 when sqlite3_column_name gives NULL (out of memory). */
long long sqlite_ffi_column_name_len(long long stmt, long long i) {
  const char* name = sqlite3_column_name(STMT(stmt), (int)i);
  return name == NULL ? -1 : (long long)strlen(name);
}

long long sqlite_ffi_column_name_byte(long long stmt, long long i, long long k) {
  return (long long)(unsigned char)sqlite3_column_name(STMT(stmt), (int)i)[k];
}
