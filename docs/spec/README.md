# Original Specification

These two documents are the **input brief** this project was built from — not
live documentation, and not updated as the implementation evolved. Where they
disagree with the rest of `docs/`, the rest of `docs/` describes what was
actually built; these describe what was asked for.

They are useful for exactly one thing: checking a specific requirement against
its original wording. Everything explaining *why* the implementation is shaped
the way it is lives in [DECISIONS.md](../DECISIONS.md); everything explaining
*what exists and what's proven* lives in
[PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

| File | What it is |
|---|---|
| [research-architecture.md](research-architecture.md) | "Agent Budget Controller: Deep Research and Production Architecture" — the research and design-rationale document. Competitive positioning, the core financial invariant argument, and the recommended production architecture. |
| [master-prompt.md](master-prompt.md) | "Agent Budget Controller — Claude Code Master Implementation Prompt" — the numbered, testable implementation requirements (money representation, token governance, budget hierarchy, the acceptance test suite, the Definition of Done). |

## Provenance note

At the time these were vendored into the repository, two additional variants
existed alongside the canonical pair above, both left behind deliberately:

- A byte-identical duplicate of the master prompt.
- An earlier, shorter draft of the master prompt (differently numbered
  sections, narrower scope) that was superseded by the version copied here.

Only the two canonical, most-current documents were copied in, to avoid three
near-identical specs drifting further apart inside the repository.
