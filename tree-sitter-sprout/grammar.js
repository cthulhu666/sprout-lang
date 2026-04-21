// SPDX-License-Identifier: MIT
//
// Scaffold for a Sprout tree-sitter grammar.
//
// This grammar is intentionally conservative. It aims to recover top-level
// declarations and common expression forms first, while leaving layout-sensitive
// parsing to an external scanner.

const commaSep = (rule) => seq(rule, repeat(seq(",", rule)));

module.exports = grammar({
  name: "sprout",

  extras: ($) => [/\s/, $.comment],

  conflicts: ($) => [
    [$.type_expression, $.type_atom],
    [$.tuple_expression, $.parenthesized_expression],
    [$.type_atom, $.qualified_identifier],
    [$.type_constructor, $.type_atom, $.qualified_identifier],
    [$.expression, $.qualified_identifier],
    [$.expression, $.expression_atom],
    [$.expression_atom, $.qualified_identifier],
    [$.expression, $.expression_atom, $.qualified_identifier],
    [$.pattern, $.qualified_identifier],
    [$.expression, $.expression_atom, $.constructor_pattern],
    [$.expression, $.constructor_pattern],
    [$.literal, $.literal_pattern],
    [$.expression, $.pattern, $.qualified_identifier],
    [$.expression_atom, $.constructor_pattern],
  ],

  externals: ($) => [
    $.newline,
    $.indent,
    $.dedent,
  ],

  word: ($) => $.identifier,

  rules: {
    source_file: ($) => repeat(choice($.declaration, $.newline)),

    comment: ($) => token(seq("#", /.*/)),

    declaration: ($) => choice(
      $.module_declaration,
      $.import_declaration,
      $.export_declaration,
      $.type_declaration,
      $.class_declaration,
      $.instance_declaration,
      $.function_declaration,
      $.let_declaration,
    ),

    module_declaration: ($) => seq(
      "module",
      field("name", $.qualified_identifier),
    ),

    import_declaration: ($) => seq(
      "import",
      field("module", $.qualified_identifier),
      optional(seq("as", $.identifier)),
      optional(seq("(", optional(commaSep($.identifier)), ")")),
    ),

    export_declaration: ($) => seq(
      "export",
      choice(
        $.exported_type_declaration,
        $.exported_class_declaration,
        $.exported_instance_declaration,
        $.exported_function_declaration,
        $.exported_let_declaration,
      ),
    ),

    exported_type_declaration: ($) => seq(
      "type",
      optional("alias"),
      field("name", $.identifier),
      repeat($.identifier),
      "=",
      choice($.record_type, $.type_expression, $.sum_type),
    ),

    exported_class_declaration: ($) => prec.right(seq(
      "class",
      field("name", $.identifier),
      repeat1($.identifier),
      optional(seq("where", $.constraint_list)),
      optional($.class_body),
    )),

    exported_instance_declaration: ($) => prec.right(seq(
      "instance",
      field("constraint", $.constraint),
      optional(seq("where", $.constraint_list)),
      optional($.instance_body),
    )),

    exported_function_declaration: ($) => seq(
      "fn",
      field("name", $.identifier),
      "(",
      optional(commaSep($.param)),
      ")",
      optional(seq("->", $.type_expression)),
      optional(seq("where", $.constraint_list)),
      "=",
      $.expression,
    ),

    exported_let_declaration: ($) => seq(
      "let",
      field("name", $.identifier),
      "=",
      $.expression,
    ),

    type_declaration: ($) => seq(
      "type",
      optional("alias"),
      field("name", $.identifier),
      repeat($.identifier),
      "=",
      choice(
        $.record_type,
        $.type_expression,
        $.sum_type,
      ),
    ),

    sum_type: ($) => seq(
      optional("|"),
      $.type_constructor,
      repeat(seq("|", $.type_constructor)),
    ),

    record_type: ($) => seq(
      "{",
      optional(commaSep($.record_field_type)),
      "}",
    ),

    record_field_type: ($) => seq(
      $.identifier,
      ":",
      $.type_expression,
    ),

    type_constructor: ($) => seq(
      field("name", $.identifier),
      repeat($.type_atom),
    ),

    class_declaration: ($) => prec.right(seq(
      "class",
      field("name", $.identifier),
      repeat1($.identifier),
      optional(seq("where", $.constraint_list)),
      optional($.class_body),
    )),

    class_body: ($) => seq(
      $.newline,
      $.indent,
      repeat1($.class_method_signature),
      $.dedent,
    ),

    class_method_signature: ($) => seq(
      "fn",
      $.identifier,
      "(",
      optional(commaSep($.param)),
      ")",
      "->",
      $.type_expression,
    ),

    instance_declaration: ($) => prec.right(seq(
      "instance",
      field("constraint", $.constraint),
      optional(seq("where", $.constraint_list)),
      optional($.instance_body),
    )),

    instance_body: ($) => seq(
      $.newline,
      $.indent,
      repeat1($.instance_method),
      $.dedent,
    ),

    instance_method: ($) => seq(
      "fn",
      $.identifier,
      "(",
      optional(commaSep($.param)),
      ")",
      optional(seq("->", $.type_expression)),
      "=",
      $.expression,
    ),

    function_declaration: ($) => seq(
      "fn",
      field("name", $.identifier),
      "(",
      optional(commaSep($.param)),
      ")",
      optional(seq("->", $.type_expression)),
      optional(seq("where", $.constraint_list)),
      "=",
      $.expression,
    ),

    let_declaration: ($) => seq(
      "let",
      field("name", $.identifier),
      "=",
      $.expression,
    ),

    constraint_list: ($) => seq($.constraint, repeat(seq(",", $.constraint))),

    constraint: ($) => seq(
      field("class", $.identifier),
      repeat($.type_atom),
    ),

    param: ($) => seq(
      field("name", $.identifier),
      optional(seq(":", $.type_expression)),
    ),

    type_expression: ($) => choice(
      $.type_arrow,
      $.type_apply,
      $.type_tuple,
      $.type_atom,
    ),

    type_arrow: ($) => seq(
      $.type_apply,
      "->",
      $.type_expression,
    ),

    type_apply: ($) => prec.left(1, seq(
      $.type_atom,
      repeat1($.type_atom),
    )),

    type_tuple: ($) => seq(
      "(",
      commaSep($.type_expression),
      ")",
    ),

    type_atom: ($) => choice(
      $.qualified_identifier,
      $.identifier,
      $.type_tuple,
    ),

    expression: ($) => choice(
      $.if_expression,
      $.match_expression,
      $.do_expression,
      $.lambda_expression,
      $.call_expression,
      $.binary_expression,
      $.unary_expression,
      $.tuple_expression,
      $.list_expression,
      $.record_expression,
      $.field_access_expression,
      $.literal,
      $.qualified_identifier,
      $.identifier,
    ),

    if_expression: ($) => seq(
      "if",
      $.expression,
      "then",
      $.expression,
      "else",
      $.expression,
    ),

    match_expression: ($) => seq(
      "match",
      $.expression,
      "with",
      $.newline,
      $.indent,
      repeat1($.match_branch),
      $.dedent,
    ),

    match_branch: ($) => seq(
      "|",
      $.pattern,
      "->",
      $.expression,
    ),

    do_expression: ($) => seq(
      "do",
      $.newline,
      $.indent,
      repeat1($.do_step),
      $.dedent,
    ),

    do_step: ($) => choice(
      $.do_let_step,
      $.do_bind_step,
      $.do_expr_step,
    ),

    do_let_step: ($) => seq(
      "let",
      $.identifier,
      "=",
      $.expression,
    ),

    do_bind_step: ($) => seq(
      $.pattern,
      "<-",
      $.expression,
    ),

    do_expr_step: ($) => $.expression,

    lambda_expression: ($) => seq(
      "\\",
      choice(
        seq("(", optional(commaSep($.param)), ")"),
        $.identifier,
      ),
      "->",
      $.expression,
    ),

    call_expression: ($) => prec.left(10, seq(
      $.expression_atom,
      repeat1(seq("(", optional(commaSep($.expression)), ")")),
    )),

    binary_expression: ($) => choice(
      prec.left(1, seq($.expression, "||", $.expression)),
      prec.left(2, seq($.expression, "&&", $.expression)),
      prec.left(3, seq($.expression, choice("==", "!=", "<", "<=", ">", ">="), $.expression)),
      prec.left(4, seq($.expression, choice("+", "-", "++"), $.expression)),
      prec.left(5, seq($.expression, choice("*", "/"), $.expression)),
    ),

    unary_expression: ($) => seq(
      choice("-", "!"),
      $.expression,
    ),

    tuple_expression: ($) => seq(
      "(",
      commaSep($.expression),
      ")",
    ),

    list_expression: ($) => seq(
      "[",
      optional(commaSep($.expression)),
      "]",
    ),

    record_expression: ($) => seq(
      $.identifier,
      "{",
      optional(commaSep($.record_field_value)),
      "}",
    ),

    record_field_value: ($) => seq(
      $.identifier,
      "=",
      $.expression,
    ),

    field_access_expression: ($) => seq(
      "get",
      $.expression,
      $.identifier,
    ),

    expression_atom: ($) => choice(
      $.literal,
      $.identifier,
      $.qualified_identifier,
      $.tuple_expression,
      $.list_expression,
      $.record_expression,
      $.field_access_expression,
      $.parenthesized_expression,
    ),

    parenthesized_expression: ($) => seq(
      "(",
      $.expression,
      ")",
    ),

    literal: ($) => choice(
      $.int_literal,
      $.string_literal,
      $.char_literal,
      $.bool_literal,
      $.unit_literal,
    ),

    int_literal: ($) => /[0-9]+/,

    string_literal: ($) => /"([^"\\]|\\.)*"/,

    char_literal: ($) => /'([^'\\]|\\.)'/,

    bool_literal: ($) => choice("true", "false"),

    unit_literal: ($) => seq("(", ")"),

    pattern: ($) => choice(
      $.wildcard_pattern,
      $.literal_pattern,
      $.tuple_pattern,
      $.constructor_pattern,
      $.identifier,
    ),

    wildcard_pattern: ($) => "_",

    literal_pattern: ($) => choice(
      $.int_literal,
      $.string_literal,
      $.char_literal,
      $.bool_literal,
      $.unit_literal,
    ),

    tuple_pattern: ($) => seq(
      "(",
      commaSep($.pattern),
      ")",
    ),

    constructor_pattern: ($) => prec.right(seq(
      $.qualified_identifier,
      repeat($.pattern),
    )),

    qualified_identifier: ($) => seq(
      $.identifier,
      repeat(seq(".", $.identifier)),
    ),

    identifier: ($) => /[A-Za-z_][A-Za-z0-9_]*/,
  },
});
