; SPDX-License-Identifier: MIT
;
; Tag queries for Sprout declarations.
; These are intended to help downstream tooling recover symbols for indexing.

(module_declaration
  (qualified_identifier) @name)

(import_declaration
  (qualified_identifier) @name)

(exported_function_declaration
  name: (identifier) @name)

(function_declaration
  name: (identifier) @name)

(exported_let_declaration
  name: (identifier) @name)

(let_declaration
  name: (identifier) @name)

(exported_type_declaration
  name: (identifier) @name)

(type_declaration
  name: (identifier) @name)

(exported_class_declaration
  name: (identifier) @name)

(class_declaration
  name: (identifier) @name)

(exported_instance_declaration
  (constraint
    class: (identifier) @name))

(instance_declaration
  (constraint
    class: (identifier) @name))

(type_constructor
  name: (identifier) @name)
