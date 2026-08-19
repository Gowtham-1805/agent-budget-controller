# Operations

Runbook: run it, test it, deploy it, smoke-test it, and what to do when
something looks wrong.

---

## Prerequisites

| Tool | Verified version | Required for |
|---|---|---|
| Python | 3.12+ | Everything |
| Node.js | 24+ | Dashboard only |
| Terraform | 1.15+ | Deployment only |
| AWS CLI | 2.36+ | Deployment only |
| Docker | — | Container build only (not installed on the reference dev machine — see [gaps](#known-gaps-on-this-development-machine)) |

`make` itself is **not installed** on the Windows reference machine this project
was built on. Every `make <target>` command below has a raw equivalent shown
alongside it — use whichever is available to you.

---

## Local development

### First-time setup

```bash
make setup
# equivalent:
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -e ".[dev,providers,observability]"

cp .env.example .env
# then edit .env with your own values
```

### Run the gateway (in-memory store, no AWS needed)

```bash
make run
# equivalent (PowerShell/Windows):
$env:ABC_USE_MEMORY_STORE="true"; $env:ABC_ENVIRONMENT="local"
.venv\Scripts\python.exe -m uvicorn abc_gateway.main:create_app --factory --reload `
  --app-dir apps\gateway\src --port 8080
```

To exercise anything through it (the demo, manual `curl`, the dashboard), you
also need a provider registered — the fastest option is the deterministic fake:

```bash
ABC_ENABLE_FAKE_PROVIDER=true   # in addition to the above
```

Confirm it's actually up:

```bash
curl http://127.0.0.1:8080/healthz    # {"status": "ok", ...}
curl http://127.0.0.1:8080/readyz     # {"status": "ready", "checks": {...}}
```

### Run the dashboard

```bash
cd apps/dashboard
npm install
GATEWAY_URL=http://127.0.0.1:8080 \
ABC_ADMIN_API_KEY=local-admin-key \
DASHBOARD_SCOPES="TEAM:engineering,AGENT:code-review" \
DASHBOARD_AGENTS="code-review" \
npm run dev
```

Then open `http://localhost:3000`. See
[apps/dashboard/README.md](../apps/dashboard/README.md) for why
`DASHBOARD_AGENTS` matters — the ledger is partitioned per agent, so the
dashboard needs to know which agents to ask.

### Run the full stack with Docker (real DynamoDB semantics)

```bash
make up      # docker compose up --build -d
make logs    # docker compose logs -f gateway
make down    # docker compose down -v
```

This runs the gateway against **DynamoDB Local**, so the reserve/reconcile
lifecycle is exercised against real transaction semantics — including genuine
`TransactionConflict`, which the in-process backend cannot produce — without an
AWS account or any spend. See [known gaps](#known-gaps-on-this-development-machine)
if Docker is unavailable to you.

---

## Running the tests

```bash
make test               # everything — ~30s
make test-unit
make test-contract      # both storage backends
make test-concurrency
make test-property
make test-failure
make test-acceptance
make test-e2e
make check              # lint + typecheck + test
```

See [TESTING.md](TESTING.md) for what each suite actually proves.

---

## The demo

Walks nine governance behaviours end to end against a running gateway, asserting
at each step. Requires the fake provider (or real credentials — see below).

```bash
# Terminal 1
ABC_USE_MEMORY_STORE=true ABC_ENVIRONMENT=local ABC_ADMIN_API_KEY=local-admin-key \
ABC_ENABLE_FAKE_PROVIDER=true \
.venv/Scripts/python.exe -m uvicorn abc_gateway.main:create_app --factory \
  --app-dir apps/gateway/src --host 127.0.0.1 --port 8080

# Terminal 2
PYTHONIOENCODING=utf-8 \
GATEWAY_URL=http://127.0.0.1:8080 \
ABC_ADMIN_API_KEY=local-admin-key \
python scripts/demo.py
```

`PYTHONIOENCODING=utf-8` is required on Windows — the demo's box-drawing output
(`──`, `✓`, `✗`) does not encode in the default `cp1252` console codepage and the
script crashes with `UnicodeEncodeError` without it. This is a real trap that was
hit during development; without the override the script fails before printing
anything useful.

Point `DEMO_PROVIDER` / `DEMO_MODEL` / `DEMO_FALLBACK_MODEL` at real catalog
entries to spend real money instead of using the fixture provider.

---

## Restarting the local gateway (Windows)

A trap hit during development, worth knowing about before it costs you a
confusing debugging session: **the PID Bash reports for a backgrounded job is
not reliably the real Windows process holding the socket**, under Git
Bash/MSYS. `kill <bash-job-pid>` can be a silent no-op — the old process keeps
running, a new one fails to bind and exits quietly, and every subsequent request
is served by stale, unfixed code.

If a restart doesn't seem to have taken effect, verify against the platform's
own view rather than trusting the shell:

```bash
netstat -ano | grep ':8080' | grep LISTENING     # the REAL Windows PID
taskkill //F //PID <that number>                  # not the bash job number
```

Then confirm the new process's own log shows a clean bind (`INFO: Uvicorn
running on http://...`), not `[Errno 10048] ... only one usage of each socket
address is normally permitted` — that error means the old process is still
alive and the new one silently exited.

Full account: [FINDINGS.md #F4](FINDINGS.md#f4-netstats-pid-is-not-bashs-job-number).

---

## Deploying to AWS

```bash
aws configure          # credentials stay on your machine
make tf-init
make tf-validate
make tf-plan
make deploy             # terraform apply — creates billable resources
```

**Cost, if left running:** roughly $60–90/month. The two largest components are
NAT gateways (~$32/mo *per availability zone*) and the ALB (~$16/mo). Set
`enable_nat_gateway=false` in `infrastructure/terraform/variables.tf` to run
Fargate tasks in public subnets behind a restrictive security group instead —
materially cheaper, and acceptable when the tasks hold no data of their own.
DynamoDB on-demand billing means idle cost is close to $0.

```bash
make destroy    # when you are finished
```

The DynamoDB tables carry `prevent_destroy` in Terraform — deleting live budget
state and the immutable ledger has to be a deliberate, separate act.

### Deployed smoke test

```bash
GATEWAY_URL=<your-alb-dns-name> \
ABC_ADMIN_API_KEY=<your-admin-key> \
make smoke
```

Proves, against the real deployment: liveness, readiness, a real governed
provider request, that a rejected request costs exactly zero committed spend,
and that state actually persists in DynamoDB.

---

## Closing the unverified items

Three things are documented as **NOT VERIFIED** because the credentials/tooling
to verify them were unavailable during development (see
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md#what-has-not-been-proven)). Here is
exactly what closes each one:

### AWS deployment

```bash
aws configure
make tf-init && make tf-plan && make deploy
```

### Live provider E2E

Add real keys to `.env`:

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

Then run the demo with `DEMO_PROVIDER=openai DEMO_MODEL=gpt-4o-mini` (or
`anthropic` / `claude-haiku-4-5`) against a gateway started **without**
`ABC_ENABLE_FAKE_PROVIDER` — this will spend real, small amounts of money (a few
cents for the full demo).

### Container build

```bash
winget install --id Docker.DockerDesktop --source winget
make build       # docker build -t agent-budget-controller:local .
make up          # full stack via docker-compose
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/readyz` returns `503`, `providers_configured: false` | No provider adapter is registered | Set `ABC_ENABLE_FAKE_PROVIDER=true`, or supply `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, or `ABC_BEDROCK_ENABLED=true` |
| Every request returns `404 provider '...' is not configured` | The agent's `routing.provider` doesn't match a registered adapter | Check `/readyz`'s `detail.providers` against the agent's routing config |
| `GET /v1/ledger` returns `{"entries": []}` unexpectedly | Called with an admin credential and no `agent_id` | This is now a `422`, not a silent empty result — see [FINDINGS.md #F3](FINDINGS.md#f3-an-admin-ledger-query-silently-answered-a-different-question). Pass `?agent_id=...` |
| A "fixed" bug still reproduces after restarting | Stale process still holding the port | See [restarting the local gateway](#restarting-the-local-gateway-windows) above |
| Demo script crashes with `UnicodeEncodeError` | Windows console codepage | `PYTHONIOENCODING=utf-8` |
| Gateway refuses to start with `use_memory_store` set | `ABC_ENVIRONMENT` is `prod`/`production` | The in-memory store is refused outright in production by design — see [DECISIONS.md](DECISIONS.md) — use `ABC_USE_MEMORY_STORE=false` with real DynamoDB |
| `tiktoken` hangs or fails on first use | First-use BPE table download | Cache it once (`TIKTOKEN_CACHE_DIR`), as CI does — see `.github/workflows/ci.yml` |

---

## Known gaps on this development machine

Recorded honestly rather than glossed over, per
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md#what-has-not-been-proven):

- **Docker is not installed.** The `Dockerfile` and `docker-compose.yml` are
  written and reviewed but have never actually been built or run.
- **No AWS credentials are configured.** `terraform validate` passes; nothing
  has been `apply`'d.
- **No `make` binary.** Every target has a raw-command equivalent documented
  above.
- **This directory is not a git repository.** `.gitignore` and
  `.github/workflows/` exist, but `git status` reports "not a git repository" —
  the CI and deploy workflows have never actually run. `git init` is a
  prerequisite for using them, and is a decision separate from this
  documentation pass.

---

## Further reading

- [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) — the five-minute orientation
- [TESTING.md](TESTING.md) — what each test suite proves
- [FINDINGS.md](FINDINGS.md) — the traps recorded here, in full
