// SPDX-License-Identifier: MIT
//
// Layout scanner placeholder for Sprout.
//
// Sprout is indentation-sensitive. A complete tree-sitter grammar will need an
// external scanner that emits newline/indent/dedent tokens. This file is a
// scaffold so the expected boundary is explicit.

#include <tree_sitter/parser.h>
#include <stdbool.h>

void *tree_sitter_sprout_external_scanner_create(void) {
  return NULL;
}

void tree_sitter_sprout_external_scanner_destroy(void *payload) {
  (void)payload;
}

unsigned tree_sitter_sprout_external_scanner_serialize(void *payload, char *buffer) {
  (void)payload;
  (void)buffer;
  return 0;
}

void tree_sitter_sprout_external_scanner_deserialize(void *payload, const char *buffer, unsigned length) {
  (void)payload;
  (void)buffer;
  (void)length;
}

bool tree_sitter_sprout_external_scanner_scan(void *payload, TSLexer *lexer, const bool *valid_symbols) {
  (void)payload;
  (void)lexer;
  (void)valid_symbols;
  return false;
}
