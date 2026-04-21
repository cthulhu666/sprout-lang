// SPDX-License-Identifier: MIT
//
// Minimal layout-sensitive scanner for Sprout.
//
// This scanner emits newline, indent, and dedent tokens. It is intentionally
// conservative: it only models leading indentation and does not try to
// interpret comments or nested layout contexts.

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <tree_sitter/parser.h>

enum TokenType {
  NEWLINE,
  INDENT,
  DEDENT,
};

typedef struct {
  uint16_t stack[64];
  uint8_t depth;
  uint8_t pending_dedents;
} Scanner;

static void scanner_reset(Scanner *scanner) {
  scanner->stack[0] = 0;
  scanner->depth = 1;
  scanner->pending_dedents = 0;
}

static uint16_t current_indent(const Scanner *scanner) {
  return scanner->stack[scanner->depth - 1];
}

static void push_indent(Scanner *scanner, uint16_t indent) {
  if (scanner->depth < sizeof(scanner->stack) / sizeof(scanner->stack[0])) {
    scanner->stack[scanner->depth++] = indent;
  }
}

static void pop_indent(Scanner *scanner) {
  if (scanner->depth > 1) {
    scanner->depth--;
  }
}

void *tree_sitter_sprout_external_scanner_create(void) {
  Scanner *scanner = malloc(sizeof(Scanner));
  if (scanner != NULL) {
    scanner_reset(scanner);
  }
  return scanner;
}

void tree_sitter_sprout_external_scanner_destroy(void *payload) {
  free(payload);
}

unsigned tree_sitter_sprout_external_scanner_serialize(void *payload, char *buffer) {
  Scanner *scanner = payload;
  unsigned size = 0;
  buffer[size++] = scanner->depth;
  buffer[size++] = scanner->pending_dedents;
  for (uint8_t i = 0; i < scanner->depth; i++) {
    uint16_t indent = scanner->stack[i];
    buffer[size++] = (char)(indent & 0xff);
    buffer[size++] = (char)((indent >> 8) & 0xff);
  }
  return size;
}

void tree_sitter_sprout_external_scanner_deserialize(void *payload, const char *buffer, unsigned length) {
  Scanner *scanner = payload;
  scanner_reset(scanner);
  if (length < 2) {
    return;
  }
  unsigned index = 0;
  scanner->depth = (uint8_t)buffer[index++];
  scanner->pending_dedents = (uint8_t)buffer[index++];
  if (scanner->depth == 0) {
    scanner->depth = 1;
  }
  if (scanner->depth > sizeof(scanner->stack) / sizeof(scanner->stack[0])) {
    scanner->depth = sizeof(scanner->stack) / sizeof(scanner->stack[0]);
  }
  for (uint8_t i = 0; i < scanner->depth && index + 1 < length; i++) {
    uint16_t low = (uint8_t)buffer[index++];
    uint16_t high = (uint8_t)buffer[index++];
    scanner->stack[i] = low | (high << 8);
  }
}

static void skip_horizontal_whitespace(TSLexer *lexer, uint16_t *indent) {
  while (lexer->lookahead == ' ' || lexer->lookahead == '\t') {
    *indent += (lexer->lookahead == '\t') ? 4 : 1;
    lexer->advance(lexer, true);
  }
}

bool tree_sitter_sprout_external_scanner_scan(void *payload, TSLexer *lexer, const bool *valid_symbols) {
  Scanner *scanner = payload;

  if (scanner->pending_dedents > 0 && valid_symbols[DEDENT]) {
    scanner->pending_dedents--;
    pop_indent(scanner);
    lexer->result_symbol = DEDENT;
    return true;
  }

  if (lexer->lookahead == '\n' && valid_symbols[NEWLINE]) {
    lexer->advance(lexer, true);
    lexer->result_symbol = NEWLINE;
    return true;
  }

  if (!(valid_symbols[INDENT] || valid_symbols[DEDENT])) {
    return false;
  }

  uint16_t indent = 0;
  uint16_t start_indent = current_indent(scanner);

  if (lexer->lookahead != ' ' && lexer->lookahead != '\t') {
    return false;
  }

  skip_horizontal_whitespace(lexer, &indent);

  if (lexer->lookahead == '\n' || lexer->lookahead == '\0') {
    return false;
  }

  if (indent > start_indent) {
    push_indent(scanner, indent);
    lexer->result_symbol = INDENT;
    return true;
  }

  if (indent < start_indent) {
    uint8_t target_depth = scanner->depth;
    while (target_depth > 1 && scanner->stack[target_depth - 1] > indent) {
      target_depth--;
    }
    if (target_depth < scanner->depth && scanner->stack[target_depth - 1] == indent) {
      uint8_t pops_needed = (uint8_t)(scanner->depth - target_depth);
      scanner->pending_dedents = (uint8_t)(pops_needed - 1);
      pop_indent(scanner);
      lexer->result_symbol = DEDENT;
      return true;
    }
  }

  return false;
}
