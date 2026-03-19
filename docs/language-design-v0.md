# Language Design v0: Evaluation Semantics and Rationale

## Goal
Define strict, beginner-friendly evaluation semantics for a statically typed, functional-first language inspired by Haskell but optimized for approachability.

## Decisions and Rationale

1. Function application is strict.
- Semantics: Evaluate callee first, then arguments left-to-right. All arguments are evaluated before entering the function body.
- Current v0 contract: calls support under-application. Multi-parameter
  declarations still have nested-arrow types, and `f(x)` produces a function
  value when `f` declares more parameters.
- Rationale: Deterministic order makes programs easier to reason about and debug.
- Tradeoff: Requires closure-backed partial applications and consistent runtime
  behavior across user-defined functions and builtins.

2. `let` bindings evaluate immediately.
- Semantics: `let x = expr` evaluates `expr` at bind time, then binds `x` immutably.
- Rationale: Matches user expectation that code executes where it appears.
- Tradeoff: No implicit laziness.

3. Binary operators evaluate left operand, then right operand.
- Semantics: Arithmetic and comparison operators are strict in both operands.
- Function composition operators follow the same operand evaluation order:
  `f >> g` means `\x -> g(f(x))` and `f << g` means `\x -> f(g(x))`.
- Rationale: Keeps operator behavior aligned with regular function-call evaluation.
- Tradeoff: Same optimization limits as strict call evaluation.

4. Boolean operators short-circuit.
- Semantics: `a && b` evaluates `b` only if `a` is true. `a || b` evaluates `b` only if `a` is false.
- Rationale: Familiar behavior, better performance, and practical guard-style programming.
- Tradeoff: A controlled exception to fully strict two-operand evaluation.

5. `if` evaluates only the selected branch.
- Semantics: Evaluate condition first; then evaluate exactly one branch.
- Rationale: Fundamental control-flow expectation in mainstream languages.
- Tradeoff: None significant.

6. `match` is deterministic and first-match-wins.
- Semantics: Evaluate scrutinee once; test patterns top-to-bottom; evaluate only the first matching branch.
- Rationale: Predictable behavior and explicit branch priority.
- Tradeoff: Branch order matters; compiler should warn on unreachable patterns.

7. Constructors and tuples evaluate fields left-to-right.
- Semantics: Evaluate all arguments before value construction.
- Rationale: Uniform mental model with function calls and operators.
- Tradeoff: Restricts some reordering optimizations.

8. Recursion uses the same strict call model.
- Semantics: Recursive bindings are allowed, and recursive calls evaluate strictly like normal calls.
- Rationale: Keeps one consistent execution model across all functions.
- Tradeoff: No lazy recursion behavior by default.

9. Top-level evaluation is in source order; `main` is the entrypoint.
- Semantics: Top-level bindings evaluate in file order; program execution starts at `main`.
- Rationale: Simple initialization model with a clear start location.
- Tradeoff: To keep modules predictable in v0, top-level `let` bindings must be pure and effectful initialization is pushed into functions.

10. Type errors are compile-time; runtime errors are minimized.
- Semantics: Static type errors fail compilation. Runtime failures are reserved for unavoidable cases (for example, explicit panic or failed external IO).
- Rationale: Safety and trustworthiness are core to the language value proposition.
- Tradeoff: Requires stronger type-checker implementation and high-quality diagnostics.

11. Effects are attached to function types in v0.
- Semantics: effectful functions are annotated explicitly, for example `fn main() -> Unit !{IO} = ...`; omitted effect annotations mean purity.
- Rationale: This keeps runtime interaction explicit without changing Sprout's strict execution model.
- Tradeoff: v0 currently supports closed effects with the built-in `IO` label plus restricted singleton effect variables such as `!{e}`; mixed/open rows and richer effect labels are still deferred.

## v0 Positioning
- Default evaluation strategy: strict.
- Beginner promise: explicit behavior, deterministic execution, clear diagnostics.
- Annotation stance: infer types wherever they can be determined unambiguously without compromising implementation simplicity, predictable behavior, or diagnostic quality; keep explicit annotations available for clarity.
- v0 effect model: functions are pure by default; effectful functions use explicit `!{IO}` annotations, and higher-order helpers may abstract over a single effect variable with `!{e}`.
- Future extension path: mixed/open effect rows, additional effect labels, and explicit laziness can be added in later milestones without changing default strict evaluation.
