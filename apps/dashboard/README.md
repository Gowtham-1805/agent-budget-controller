# Dashboard

The operator UI. Next.js 15, App Router, server components that call the
gateway directly — no client-side API calls, so the admin credential never
reaches the browser.

See [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for how this fits
into the rest of the system, and
[../../docs/PROJECT_CONTEXT.md](../../docs/PROJECT_CONTEXT.md) for the project
as a whole.

---

## Why it queries the ledger per-agent, not tenant-wide

**This is the single most important thing to understand before touching this
code.** `GET /v1/ledger` is partitioned by agent — in DynamoDB it's a `Query`
against a per-agent GSI, not a tenant-wide scan (see
[../../docs/DATA_MODEL.md](../../docs/DATA_MODEL.md#item-layout-abc_ledger)).
There is no single endpoint that returns "everything for this tenant."

So "recent requests across the tenant" on the Overview page, and the default
view on the Ledger page, are both assembled by fetching each agent named in
`DASHBOARD_AGENTS` and merging client-side (`app/page.tsx`,
`app/ledger/page.tsx`). If `DASHBOARD_AGENTS` is empty, both pages show an
honest note explaining why, rather than a silently-empty table that looks
identical to "nothing has happened yet."

This distinction cost real debugging time during development — see
[../../docs/FINDINGS.md #F3](../../docs/FINDINGS.md#f3-an-admin-ledger-query-silently-answered-a-different-question)
for the backend bug this uncovered (an admin querying without `agent_id` used to
get a silent empty result instead of an error).

---

## Pages

| Route | Shows |
|---|---|
| `/` | Overview — tracked budget scopes (`DASHBOARD_SCOPES`) as cards, plus recent requests merged across `DASHBOARD_AGENTS` |
| `/agents` | One `BudgetCard` per agent in `DASHBOARD_AGENTS` — committed vs. reserved vs. available |
| `/ledger` | Full ledger for one agent — `?agent=<id>` or defaults to the first `DASHBOARD_AGENTS` entry |
| `/events` | The permanent audit trail — every pause/resume, actor, and reason |

`components/BudgetCard.tsx` is the one piece of UI worth reading closely: it
renders committed and reserved as visually distinct bars specifically because
conflating them is the mistake the entire backend exists to prevent — a scope
can be well under its committed limit while almost all remaining capacity is
already promised to in-flight requests.

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `GATEWAY_URL` | Yes | Base URL of the gateway, e.g. `http://127.0.0.1:8080` |
| `ABC_ADMIN_API_KEY` | Yes | Admin credential. Server-side only — never sent to the browser |
| `DASHBOARD_SCOPES` | For the Overview page | Comma-separated `TYPE:id` pairs, e.g. `TEAM:engineering,AGENT:code-review` |
| `DASHBOARD_AGENTS` | For ledger views | Comma-separated agent ids to query the ledger for — see above |
| `NEXT_PUBLIC_WEBSOCKET_URL` | No | Live-update endpoint. Falls back to polling if unset |

---

## Running it

```bash
npm install
GATEWAY_URL=http://127.0.0.1:8080 \
ABC_ADMIN_API_KEY=local-admin-key \
DASHBOARD_SCOPES="TEAM:engineering,AGENT:code-review" \
DASHBOARD_AGENTS="code-review" \
npm run dev
```

Then open `http://localhost:3000`. The gateway must already be running — see
[../../docs/OPERATIONS.md](../../docs/OPERATIONS.md).

```bash
npm run build       # production build (standalone output)
npm run typecheck    # tsc --noEmit, strict mode
```

TypeScript strict mode with `noUncheckedIndexedAccess` is the real quality gate
here — it caught two genuine nullability bugs during development. ESLint is not
installed and is deliberately skipped in the production build config
(`next.config.mjs`) rather than failing the build for a missing dependency.

---

## Live updates

`lib/useLiveBudgets.ts` connects to `NEXT_PUBLIC_WEBSOCKET_URL` when set, and
falls back to polling when it isn't. Two properties matter in that hook,
because getting either wrong means the dashboard lies to the operator:

- **Duplicate and out-of-order events are discarded by version number.** The
  transport gives at-least-once delivery with no ordering guarantee.
- **A reconnect refetches from the gateway rather than resuming the stream.**
  Events that arrived while disconnected are gone; the gateway's current state
  is the only thing that's actually authoritative.
