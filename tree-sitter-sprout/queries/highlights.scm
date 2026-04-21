; SPDX-License-Identifier: MIT
;
; Basic syntax highlighting for Sprout.

; Keywords
(module_declaration "module" @keyword)
(import_declaration "import" @keyword)
(import_declaration "as" @keyword)
(export_declaration "export" @keyword)
(type_declaration "type" @keyword)
(type_declaration "alias" @keyword)
(class_declaration "class" @keyword)
(instance_declaration "instance" @keyword)
(function_declaration "fn" @keyword)
(let_declaration "let" @keyword)
(if_expression "if" @keyword)
(if_expression "then" @keyword)
(if_expression "else" @keyword)
(match_expression "match" @keyword)
(match_expression "with" @keyword)
(do_expression "do" @keyword)
(lambda_expression "\\" @operator)

; Literals
(int_literal) @number
(string_literal) @string
(char_literal) @string
(bool_literal) @constant.builtin
(unit_literal) @punctuation.special

; Names
(identifier) @variable
(qualified_identifier
  (identifier) @module)
(type_constructor
  name: (identifier) @type)
(constraint
  class: (identifier) @type)

; Operators and punctuation
(binary_expression ["||" "&&" "==" "!=" "<" "<=" ">" ">=" "+" "-" "++" "*" "/"] @operator)
(unary_expression ["-" "!"] @operator)
(field_access_expression "get" @keyword)
(tuple_expression ["," "(" ")"] @punctuation.delimiter)
(list_expression ["[" "]" ","] @punctuation.delimiter)
(record_expression ["{" "}"] @punctuation.bracket)
(class_body ["{" "}"] @punctuation.bracket)
(instance_body ["{" "}"] @punctuation.bracket)
