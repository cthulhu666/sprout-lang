# Bootstrap Debugging Tools

This document has been split into two focused docs:

- **[compiler-internals.md](compiler-internals.md)** — GC ABI invariants, type-aware rooting, GC safety linter. Read this before editing `stdlib/compiler/` or `runtime/`.
- **[debugging.md](debugging.md)** — Diagnostic phases, `just llvm-where`, 2-step bootstrap protocol. Read this when something is broken.
