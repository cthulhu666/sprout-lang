# Preliminary Analysis: Implementing a Web Browser in Sprout (No JavaScript)

## Status

This document is an early feasibility analysis, not a language proposal and not
a normative spec.

The goal is to answer two questions:

1. What kind of browser is realistically implementable in Sprout today?
2. Which missing language features would most improve that effort?

## Problem Statement

The phrase "implement a web browser in Sprout" can mean several different
targets:

- a terminal-based text browser
- an HTML renderer with keyboard navigation
- a browser engine core that parses HTML/CSS and computes layout
- a full graphical browser with windows, fonts, images, and event handling

Those targets have very different requirements. This analysis treats them
separately so the project does not accidentally scope itself into a runtime
problem and call it a language problem.

## High-Level Assessment

Sprout already appears strong enough for a meaningful browser experiment, but
the realistic first target is a text-oriented browser or browser-engine
prototype, not a full graphical browser.

Current strengths:

- algebraic data types and pattern matching
- recursive functional programming
- strings, bytes, vectors, and dictionary-like maps
- explicit `IO` effects in the current implementation
- TCP and HTTP client support
- terminal input/output support

Current limit:

- there is no graphics/windowing/rendering runtime surface

That means:

- a terminal browser is plausible
- an HTML/CSS engine prototype is plausible with some pain
- a full graphical browser is blocked primarily by runtime surface, not only by
  missing language features

## Existing Support in the Current Repository

Based on the current implementation and docs:

- Core normative v0 includes HM-style typing, ADTs, tuples, strict evaluation,
  and pattern matching.
- The implementation already includes modules and typeclasses as experimental
  extensions.
- The runtime already exposes:
  - HTTP request support
  - TCP networking
  - bytes and UTF-8 conversion helpers
  - maps and vectors
  - terminal cursor movement, key input, and line output

Relevant files:

- [spec-v0.md](/Users/cthulhu/Dev/lang/sprout_lang/docs/spec-v0.md)
- [README.md](/Users/cthulhu/Dev/lang/sprout_lang/README.md)
- [prelude.sprout](/Users/cthulhu/Dev/lang/sprout_lang/stdlib/prelude.sprout)
- [http_client.sprout](/Users/cthulhu/Dev/lang/sprout_lang/stdlib/http_client.sprout)
- [http.sprout](/Users/cthulhu/Dev/lang/sprout_lang/stdlib/http.sprout)
- [bytes.sprout](/Users/cthulhu/Dev/lang/sprout_lang/stdlib/bytes.sprout)
- [string.sprout](/Users/cthulhu/Dev/lang/sprout_lang/stdlib/string.sprout)
- [terminal.sprout](/Users/cthulhu/Dev/lang/sprout_lang/stdlib/terminal.sprout)

## Capability Matrix

| Browser Subsystem | Current Support | Main Gaps | Severity |
| --- | --- | --- | --- |
| URL input and navigation state | Strings, ADTs, `Maybe`/`Result`, `Dict`, `Vec` | Records, better string helpers, URL stdlib | Medium |
| HTTP fetch | `http_request`, `stdlib.http_client`, TCP/bytes/runtime IO | Redirect handling policy, header helpers, caching/storage | Low |
| HTML tokenization | Strings, recursion, pattern matching, tuples, vectors | Records, text primitives, range/index ergonomics, Unicode story | High |
| HTML tree / DOM | ADTs are sufficient in principle | Records, immutable update ergonomics, collection helpers | High |
| CSS tokenization/parsing | Same as HTML parser foundations | Records, richer string helpers, collection helpers | High |
| Style resolution | Dict/Map, ADTs, recursion | Records, ordered rule structures, richer collection APIs | Medium |
| Layout tree | ADTs, recursion, tuples, integers | Records, cleaner state threading, possible float support later | High |
| Text rendering in terminal | `stdlib.terminal`, line rendering, key input | Wrapping helpers, scrolling model, viewport abstraction | Medium |
| Link selection and interaction | Terminal key input, strings, IO | Better TUI abstractions, navigation history helpers | Medium |
| Forms without JS | HTTP POST, strings, headers | Form encoding helpers, input widgets, state records | Medium |
| Persistent cache and history | Some file reads exist | Better file IO surface, directories/paths, serialization helpers | Medium |
| Incremental or async loading | Strict eval plus `IO` | Concurrency/event loop/runtime model | Low for MVP, high later |
| Images | No practical support | Graphics runtime, image decoding, layout integration | Critical for full browser |
| Fonts and graphical rendering | No support | Windowing, drawing, font metrics, rasterization | Critical for full browser |

## Missing Language Features

This section focuses on language-level gaps, not host-runtime gaps.

### 1. Records

This is the single most important missing feature for browser implementation.

Browser code naturally wants named fields for:

- DOM nodes
- parser state
- CSS rules and declarations
- style values
- layout boxes
- navigation state
- form state

Without records, the implementation must lean on tuples or many small ADTs,
which becomes hard to read and maintain quickly.

Impact: very high

### 2. Ergonomic Immutable Record Updates

Even if records exist, browser code needs cheap and readable immutable updates.

Parsers and layout passes continuously transform a mostly-stable state with one
or two changed fields. Without update ergonomics, code becomes dominated by
manual reconstruction boilerplate.

Impact: very high once records exist

### 3. Richer String and Text Processing

Current string support is enough for small examples, but browser work is parser
heavy and scanner heavy.

Useful additions would likely include:

- `ends_with`
- `contains`
- delimiter-based split helpers
- trimming variants
- single-character classification helpers
- clearer Unicode/codepoint-level story

HTML and CSS parsing are otherwise much more verbose than necessary.

Impact: high

### 4. Better Collection Ergonomics

Sprout already has `List`, `Vec`, and `Dict`, but browser code benefits from a
broader helper surface.

Most useful likely additions:

- `vec_filter`
- `vec_find`
- `vec_concat`
- `vec_flat_map`
- better indexed folds/maps
- stable accumulation helpers

Impact: high

### 5. Range and Indexing Ergonomics

Tokenizer and parser code tends to be index-heavy. Better range or slice
ergonomics would reduce scanner verbosity and off-by-one risk.

This is not a hard blocker, but it has high leverage.

Impact: medium

### 6. Strong Diagnostics for Pattern-Heavy Code

This is not a browser-specific language feature, but browser code will involve
large parser ADTs and many `match` branches. Good diagnostics matter when
iterating on recursive-descent parsers and tree transformations.

Impact: medium

## Mostly Runtime Gaps, Not Language Gaps

These gaps matter, but they should not be misclassified as core-language
problems.

### 1. Graphics and Windowing

This is the main blocker for a real graphical browser.

Current terminal primitives are enough for:

- a text browser
- a terminal document viewer
- a simple browser-like TUI

They are not enough for:

- windows
- font rendering
- pixel drawing
- images
- scroll surfaces
- pointer events

### 2. Filesystem and Cache Surface

A practical browser will eventually want:

- persistent history
- local file loading
- cache storage
- downloaded asset storage

That likely needs stronger path and file APIs.

### 3. Concurrency or Event Loop Model

This is not required for a first no-JS text browser, but it becomes relevant
for:

- responsiveness
- multiple resource fetches
- incremental loading
- input handling during slow IO

### 4. Unicode and Text Rendering Contract

A browser quickly runs into text-width, Unicode, and wrapping semantics. That
problem exists even in a terminal browser.

## Smallest Viable Browser Scope

The most realistic first target is:

1. a terminal-only browser
2. HTTP GET only
3. simple redirect support
4. an HTML subset
5. no CSS initially
6. keyboard navigation between links

### Suggested HTML Subset

Initial useful tags:

- `html`
- `head`
- `body`
- `p`
- `div`
- `span`
- `h1` through `h6`
- `ul`
- `ol`
- `li`
- `a`
- `pre`
- `code`
- `strong`
- `em`

Initial simplifications:

- ignore most malformed-page recovery complexity
- ignore scripts entirely
- ignore images entirely
- ignore CSS initially
- hardcode basic block/inline behavior

This keeps the first milestone focused on parsing, structure, and rendering
without pulling in the full web platform.

## Recommended Progression

The most pragmatic sequence would be:

1. add records
2. add lightweight immutable update ergonomics
3. expand `stdlib.string`
4. expand `Vec`/collection helpers
5. add a small `stdlib.url`
6. prototype `stdlib.html` tokenizer/parser
7. build a terminal HTML viewer
8. only later consider CSS subsets

This keeps the project aligned with the rule of solving the tractable root
problem first instead of widening the runtime too early.

## Recommended Scope Boundaries

What should stay in Sprout:

- URL parsing helpers
- HTML tokenization and parsing
- DOM-like tree structures
- simple style or layout experiments
- terminal rendering logic

What likely belongs in the host runtime if the project ever targets a full
graphical browser:

- windows
- drawing surfaces
- image decoding primitives
- fonts and text measurement
- event-loop integration

## Bottom Line

The answer depends on the target.

- A terminal browser or HTML viewer in Sprout is realistic.
- A browser-engine prototype that parses HTML and computes simplified layout is
  realistic but will be much more pleasant once records exist.
- A full graphical browser is not mainly blocked by the current language core;
  it is blocked by the absence of graphics/windowing runtime support.

If the project wants to move in this direction, the highest-value language
feature to unlock it is records, followed by better text and collection
ergonomics.
