# Template Engine (v0) — design

**Status:** experimental. This document describes the design and roadmap of
`stdlib/template.sprout`, a minimal Jinja-flavored HTML template engine. It is
supporting design material, not normative — `docs/spec-v0.md` remains the source of
truth for the language core. Phase 1 (below) is the currently-implemented surface.

## 1. Problem statement

The HTTP server (`stdlib/http_server.sprout`) hands handlers a `String` body to fill.
Building HTML by string concatenation does not scale past trivial pages, and —
critically — concatenating user data into HTML is an XSS vector. We want to render
HTML from a template string plus a data context, with escaping handled by default.

## 2. Goals / non-goals

**Goals**
- Runtime-parsed templates in Jinja shape: `{{ … }}`, `{% … %}`, `{# … #}`.
- Variable interpolation with nested field access (`{{ user.name }}`).
- **HTML auto-escaping by default** (see §3), opt out with `| safe`.
- Control flow: `{% if %}` / `{% else %}` / `{% endif %}` and `{% for x in xs %}`.
- A `compile`-once / `render`-many split, so a server parses a template once and
  renders it per request.
- Reuse of the existing `json.Json` value model for the data context.

**Non-goals (deferred follow-ups)**
- Template inheritance (`extends` / `block`), includes, macros.
- A full expression language (arithmetic, comparisons, method calls).
- Contextual escaping for JS / CSS / URL positions (HTML-text escaping only).
- Whitespace control (`{%-` / `-%}`).

## 3. Prior-art survey (primary-source verified)

| Engine | Logic in templates | HTML-escape default | Opt-out |
|---|---|---|---|
| Jinja2 | rich (if/for/filters/inherit) | **off** in raw `Environment` (`autoescape=False`); Flask opts in via `select_autoescape` | `\| safe` |
| Django templates | if/for/filters | **on** — escapes `& < > " '` | `\| safe`, `{% autoescape off %}` |
| Go `html/template` | if/range/pipelines | **on, always, contextual** | typed `template.HTML` |
| Mustache / Handlebars | logic-less (sections) | **on** — `{{x}}` escapes | `{{{x}}}` / `{{& x}}` |

**Conclusion.** Every *web-facing* engine escapes HTML by default; only raw Jinja is
opt-out, because Jinja is not HTML-specific (Flask flips it on for `.html`). The only
real divergence is the opt-out *marker*. Sprout's engine is HTML-oriented, so:

- **Auto-escape is ON by default**, opt out with Jinja/Django's `| safe`.
- The escape set is Django's canonical five: `&` → `&amp;`, `<` → `&lt;`,
  `>` → `&gt;`, `"` → `&quot;`, `'` → `&#x27;`. (`&` is replaced first so the
  entities introduced by the other replacements are not double-escaped.)

## 4. Architecture

Three stages mirroring the self-hosted compiler's own lex → parse → eval shape.

**Value model = `json.Json`.** The data context is a `Json` object. `json_get_field`
is variable lookup, `json_array_next` is `{% for %}` iteration, and `json.encode(x)`
turns any `JsonEncode`-deriving record into a context. No parallel value ADT.

1. **Tokenizer** — source → a stream of `Text` / `{{…}}` / `{%…%}` / `{#…#}` tokens.
2. **Parser** — tokens → a `Node` AST by recursive descent:

   ```
   Node = NodeText String
        | NodeInterp (Vec String) Bool            # path segments, is_safe
        | NodeIf (Vec String) (Vec Node) (Vec Node)   # cond path, then, else
        | NodeFor String (Vec String) (Vec Node)      # loop var, iterable path, body
   ```

3. **Renderer** — `render(nodes, ctx)` walks the AST, resolving paths against `ctx`
   extended with `{% for %}`-bound variables (a scope overlay checked before the root
   object), escaping scalars unless `| safe`, and accumulating the output.

**Truthiness (`if`).** Following Python/Jinja: `null`, `false`, `0`, `""`, an empty
array, and an empty object are falsy; everything else is truthy.

**Scalar rendering.** `JsonString` as-is, `JsonInt` as digits, `JsonBool` as
`true`/`false`, `JsonNull` as `""`. Escaped unless marked safe.

**`for` semantics.** The iterable resolves to a `JsonArray` → one body render per
element. An undefined/null iterable yields zero iterations (lenient). A non-null,
non-array iterable is a loud error (matches Jinja's non-iterable behaviour).

**Undefined variables.** Interpolating an undefined variable renders `""` (Jinja's
lenient default). A strict mode is a possible later toggle.

## 5. Public API

```sprout
export type Template
export type TemplateError (..)
export fn compile(source: String) -> Result TemplateError Template
export fn render(tmpl: Template, context: Json) -> Result TemplateError String
export fn render_string(source: String, context: Json) -> Result TemplateError String
export fn escape_html(raw: String) -> String
```

`TemplateError` covers parse failures: unclosed `{{` / `{%` / `{#`, an unknown
statement keyword, a missing/mismatched `endif` / `endfor`, and a malformed `for`
(no `in`).

## 6. Roadmap

- **Phase 1 (implemented).** Interpolation with nested paths + auto-escape + `| safe`,
  `{# comments #}`, `{% if/else/endif %}`, `{% for x in xs %}`, and `escape_html`.
- **Phase 2.** A small filter pipeline (`upper`, `lower`, `default`, `length`) and
  loop metadata (`loop.index`). A web-server example rendering an HTML page.
- **Phase 3.** Template inheritance / includes, if demand warrants.

No builtins, runtime, or compiler changes: the engine is pure Sprout over `json.Json`
and `stdlib.string`.
