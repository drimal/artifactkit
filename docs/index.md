# artifactkit

A shared artifact-creation harness for agentic workflows. Instead of every
Strands agent, MCP server, or Python script reimplementing "generate a
document, write it somewhere, hope the write worked," they call one
service with a declarative spec and get back a verified location.

## The problem this solves

As agentic workflows spread across a team or a codebase, each one that
needs to produce a real deliverable — a report, a deck, a spreadsheet, a
generated file — tends to build its own version of the same plumbing:
render the content, pick a destination, write it, and (often skipped)
confirm the write actually landed. That plumbing is not specific to any
one workflow's domain logic. It's infrastructure, and infrastructure
duplicated N times is infrastructure tested and hardened N times less
than it should be.

## What artifactkit actually does

- **Structured content** (Word docs, PowerPoint decks, Excel workbooks,
  PDFs) is described as a plain Python spec — [`DocumentSpec`][artifactkit.DocumentSpec],
  [`PresentationSpec`][artifactkit.PresentationSpec], [`WorkbookSpec`][artifactkit.WorkbookSpec] —
  and rendered by a backend, validated, then written and verified.
- **Arbitrary files** (code, images, data, anything already fully
  formed) skip rendering entirely via [`FileBundleSpec`][artifactkit.FileBundleSpec] +
  [`RawFile`][artifactkit.RawFile], going straight to write + verify, with automatic
  zip bundling once the file count passes a threshold.
- **Destinations are pluggable.** Local disk and S3 ship built in;
  adding another means implementing the `OutputDestination` protocol,
  not touching anything else.
- **Every write is verified**, not assumed. A write that "succeeds" but
  doesn't verify (checksum/size mismatch) is treated as a failure, not
  a success with an asterisk.
- **Retries are scoped narrowly.** Only the write step retries, and
  only for exceptions the destination itself classifies as transient.
  A malformed spec or a permissions error fails immediately instead of
  burning through a retry budget on something that will never succeed.
- **Every call is observable.** See [Observability](observability.md).

## Where to go next

- New to the library? Start with [Quickstart](quickstart.md).
- Want the mental model before the API? Read [Architecture](architecture.md).
- Wiring this into a production pipeline? See [Observability](observability.md)
  for logging fields and metrics hooks.
- Looking for a specific class or method? See the [API reference](api-reference.md).
