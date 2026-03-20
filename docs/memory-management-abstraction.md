# Memory Management Abstraction Note

This note records a design constraint for Sprout: the core language should keep
memory management abstract unless and until the language intentionally exposes
resource or lifetime semantics.

It is a supporting design note, not part of the normative v0 specification.

## Motivation

Sprout may eventually want more than one execution profile built on the same
language core, for example:

- a hosted profile with garbage collection,
- a freestanding or runtime-minimal profile for games, embedded targets, or OS
  work,
- other implementation strategies such as arenas, regions, or reference
  counting.

Keeping the core memory model abstract preserves that option. It allows Sprout
to share syntax, typing, modules, and most user-facing semantics across
profiles rather than forking into separate dialects too early.

## Design Guideline

Until Sprout explicitly adopts language-level ownership, borrowing, lifetime,
destructor, or allocator semantics, design docs should treat memory management
as a runtime/profile concern rather than a core-language guarantee.

In practice, proposals should:

- avoid wording that makes garbage collection part of the core language
  contract,
- avoid assuming manual memory management is "just an implementation detail" if
  it would require visible language semantics,
- separate core-language semantics from hosted-runtime conveniences,
- make any profile-specific runtime assumptions explicit.

## Boundary

Some concerns can stay abstract:

- whether reclamation uses GC, arenas, regions, or another runtime strategy,
- how managed values are represented internally,
- which hosted runtime services are used to implement allocation and
  reclamation.

Some concerns cannot stay abstract if Sprout chooses to expose them:

- ownership or borrowing rules,
- lifetime restrictions,
- deterministic destruction semantics,
- explicit allocator APIs in ordinary user code,
- profile-specific guarantees about when values are reclaimed.

If Sprout adopts any of those visible semantics, the relevant design and
normative spec documents should define them directly rather than leaving them
implicit in implementation notes.
