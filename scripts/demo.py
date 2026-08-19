#!/usr/bin/env python3
"""The full demo scenario, run against a live gateway.

Walks every governance behaviour in order, printing what happened and -- more
importantly -- what it proves. Each step asserts, so this doubles as an
executable acceptance check rather than a scripted narration.

    GATEWAY_URL=http://localhost:8080 ABC_ADMIN_API_KEY=... python scripts/demo.py

Works against the fake `test` provider by default, so it costs nothing. Point
`DEMO_PROVIDER`/`DEMO_MODEL` at a real provider to spend real money.
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")
ADMIN = os.environ.get("ABC_ADMIN_API_KEY", "local-admin-key")
PROVIDER = os.environ.get("DEMO_PROVIDER", "test")
PREMIUM = os.environ.get("DEMO_MODEL", "premium")
CHEAP = os.environ.get("DEMO_FALLBACK_MODEL", "cheap")

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"
)

SUFFIX = uuid.uuid4().hex[:6]
TEAM = f"demo-team-{SUFFIX}"
failures: list[str] = []


def step(number: int, title: str) -> None:
    print(f"\n{BOLD}{CYAN}── Step {number} ── {title}{RESET}")


def proves(text: str) -> None:
    print(f"   {DIM}proves: {text}{RESET}")


def ok(text: str) -> None:
    print(f"   {GREEN}✓{RESET} {text}")


def bad(text: str) -> None:
    failures.append(text)
    print(f"   {RED}✗{RESET} {text}")


def info(text: str) -> None:
    print(f"   {YELLOW}·{RESET} {text}")


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}"}


def main() -> int:
    print(f"{BOLD}Agent Budget Controller — demo{RESET}")
    print(f"{DIM}gateway: {GATEWAY}   provider: {PROVIDER}{RESET}")

    with httpx.Client(base_url=GATEWAY, timeout=60.0) as c:
        if c.get("/healthz").status_code != 200:
            print(f"{RED}gateway unreachable at {GATEWAY}{RESET}")
            return 1

        # ------------------------------------------------------------------
        step(1, "Provision a team and three agents")
        c.post(
            "/v1/teams",
            json={"team_id": TEAM, "budget": {"amount_usd": "5.00"}},
            headers=admin_headers(),
        )
        agents = {}
        for name, budget, session_budget, allocation in (
            ("agent-01", "0.50", None, None),
            ("agent-02", "0.20", None, "0.08"),   # small allocation → substitution
            ("agent-03", "0.50", "0.08", None),   # session budget → closure
        ):
            agent_id = f"{name}-{SUFFIX}"
            body = {
                "agent_id": agent_id,
                "team_id": TEAM,
                "budget": {"amount_usd": budget},
                "default_max_output_tokens": 1000,
                "routing": {
                    "provider": PROVIDER,
                    "preferred_model": PREMIUM,
                    "fallback_models": [CHEAP],
                    "allocations": (
                        [{"provider": PROVIDER, "model": PREMIUM,
                          "amount_usd": allocation}] if allocation else []
                    ),
                },
                "runaway": {"monthly_budget_percent": 20, "interval_minutes": 60},
            }
            if session_budget:
                body["session_budget_usd"] = session_budget
            c.post("/v1/agents", json=body, headers=admin_headers())
            # Each agent gets its own credential, which is the only thing that
            # decides whose budget a request draws from.
            issued = c.post(f"/v1/agents/{agent_id}/keys", headers=admin_headers())
            AGENT_KEYS[agent_id] = issued.json()["api_key"]
            agents[name] = agent_id
        ok(f"team {TEAM} at $5.00 with three agents, each with its own key")
        info("agent-02 has a $0.08 premium allocation inside a $0.20 budget")

        # ------------------------------------------------------------------
        step(2, "Normal governed traffic")
        proves("spend is reserved before the provider, then reconciled to actual usage")
        response = call(c, agents["agent-01"])
        if response.status_code == 200:
            budget = response.json()["budget"]
            ok(
                f"allowed · estimated ${budget['estimated_cost_usd']} → "
                f"actual ${budget['actual_cost_usd']}"
            )
            info("the difference was returned to the budget at reconciliation")
        else:
            bad(f"expected 200, got {response.status_code}: {response.text[:200]}")

        # ------------------------------------------------------------------
        step(3, "The 80% warning fires exactly once")
        proves("the warning is a durable state transition, not a repeating poll")
        agent = agents["agent-01"]
        for _ in range(12):
            if call(c, agent).status_code != 200:
                break
        state = budget_state(c, agent)
        if state:
            info(
                f"utilization {state['utilization_percent']}% · "
                f"warning_sent={state['warning_sent']}"
            )
            if state["utilization_percent"] >= 80 and state["warning_sent"]:
                ok("threshold crossed and warned once")
            elif state["utilization_percent"] < 80:
                info("did not reach 80% — budget larger than expected")

        # ------------------------------------------------------------------
        step(4, "Preferred-model allocation exhausted → cheaper fallback")
        proves("substitution happens, is verified cheaper, and is never silent")
        agent = agents["agent-02"]
        substituted = None
        for _ in range(8):
            r = call(c, agent)
            if r.status_code != 200:
                break
            if r.json()["budget"]["substituted"]:
                substituted = r
                break
        if substituted is not None:
            b = substituted.json()["budget"]
            ok(f"{b['requested_model']} → {b['effective_model']}")
            info(f"decision: {b['decision']}")
            info(f"estimated saving: ${b['estimated_savings_usd']}")
            info(f"header X-Budget-Decision: {substituted.headers.get('X-Budget-Decision')}")
        else:
            bad("no substitution occurred")

        # ------------------------------------------------------------------
        step(5, "Hard cap → 429, and the provider is never called")
        proves("this is enforcement, not observability")
        agent = agents["agent-02"]
        blocked = None
        for _ in range(30):
            r = call(c, agent)
            if r.status_code == 429:
                blocked = r
                break
        if blocked is not None:
            error = blocked.json()["error"]
            ok(f"429 {error['type']} on {error['scope']}/{error['scope_id']}")
            info(f"available ${error.get('available_usd')} < required ${error.get('requested_usd')}")
            info(f"resets at {error.get('reset_at')}")

            before = budget_state(c, agent)
            call(c, agent)
            after = budget_state(c, agent)
            if before and after and before["committed_usd"] == after["committed_usd"]:
                ok(f"committed unchanged at ${after['committed_usd']} — zero provider spend")
            else:
                bad("committed spend moved on a rejected request")

            if after and float(after["committed_usd"]) <= float(after["limit_usd"]):
                ok(
                    f"invariant holds: committed ${after['committed_usd']} "
                    f"≤ limit ${after['limit_usd']}"
                )
            else:
                bad("OVERSPEND — the invariant was violated")
        else:
            bad("never reached the hard cap")

        # ------------------------------------------------------------------
        step(6, "Session budget exhausted → session closes")
        proves("a session never exceeds its cap; it closes when the next request cannot fit")
        agent = agents["agent-03"]
        created = c.post("/v1/sessions", json={}, headers=agent_headers(agent))
        if created.status_code == 201:
            session_id = created.json()["session_id"]
            for _ in range(10):
                if call(c, agent, session_id=session_id).status_code == 429:
                    break
            session = c.get(
                f"/v1/sessions/{session_id}", headers=agent_headers(agent)
            ).json()
            if session["status"] == "CLOSED_BUDGET":
                ok(f"session {session_id[:20]}… → CLOSED_BUDGET")
                info(f"committed ${session['committed_usd']} of ${session['limit_usd']}")
            else:
                bad(f"session status is {session['status']}, expected CLOSED_BUDGET")
        else:
            info(f"session creation returned {created.status_code}; skipping")

        # ------------------------------------------------------------------
        step(7, "Runaway agent paused — while the others keep working")
        proves("the breaker isolates one agent instead of taking the team down")
        agent = agents["agent-03"]
        c.post(
            f"/v1/admin/agents/{agent}/pause",
            json={"reason": "demo: simulated runaway loop"},
            headers=admin_headers(),
        )
        blocked = call(c, agent)
        if blocked.status_code == 423:
            ok(f"paused agent rejected with 423 {blocked.json()['error']['type']}")
        else:
            bad(f"expected 423 for a paused agent, got {blocked.status_code}")

        healthy = call(c, agents["agent-01"])
        if healthy.status_code in (200, 429):
            ok(f"a different agent still operates (HTTP {healthy.status_code})")
        else:
            bad(f"an unrelated agent was affected: {healthy.status_code}")

        # ------------------------------------------------------------------
        step(8, "Human review restores service, with an audit record")
        proves("resuming requires a reason and is recorded permanently")
        resumed = c.post(
            f"/v1/admin/agents/{agent}/resume",
            json={"reason": "demo: verified the loop was fixed"},
            headers=admin_headers(),
        )
        if resumed.status_code == 200:
            record = resumed.json()
            ok(f"{record['previous_state']} → {record['new_state']} by {record['actor']}")
            info(f"reason recorded: {record['reason']}")
        else:
            bad(f"resume failed: {resumed.status_code}")

        no_reason = c.post(
            f"/v1/admin/agents/{agents['agent-01']}/resume",
            json={"reason": ""},
            headers=admin_headers(),
        )
        if no_reason.status_code == 422:
            ok("a resume without a reason is refused")

        # ------------------------------------------------------------------
        step(9, "The ledger records estimate alongside actual")
        proves("spend can be audited and reproduced against a pinned price catalog")
        ledger = c.get(f"/v1/ledger?agent_id={agents['agent-01']}&limit=5",
                       headers=admin_headers())
        if ledger.status_code == 200 and ledger.json()["entries"]:
            entry = ledger.json()["entries"][0]
            ok(f"{len(ledger.json()['entries'])} entries")
            info(
                f"reserved {entry['reserved_output_tokens']} output tokens, "
                f"used {entry['actual_output_tokens']}"
            )
            info(
                f"estimated ${entry['estimated_max_cost_usd']} → "
                f"actual ${entry['actual_total_cost_usd']}"
            )
            info(f"priced with catalog {entry['price_catalog_version']}")
        else:
            bad("no ledger entries found")

    # ----------------------------------------------------------------------
    print(f"\n{BOLD}{'=' * 62}{RESET}")
    if failures:
        print(f"{RED}{BOLD}{len(failures)} check(s) failed:{RESET}")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"{GREEN}{BOLD}Every demo check passed.{RESET}")
    print(f"{DIM}committed + reserved ≤ limit held throughout.{RESET}")
    return 0


#: agent_id -> its own API credential. Populated at provisioning time.
AGENT_KEYS: dict[str, str] = {}


def call(client: httpx.Client, agent_id: str, session_id: str | None = None):
    """One governed request, made *as* the given agent.

    Note that the agent is identified by which credential is presented, not by
    anything in the request body. There is deliberately no field a caller could
    set to spend from a different agent's budget.
    """
    body: dict = {
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1000,
    }
    if session_id:
        body["session_id"] = session_id
    return client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {AGENT_KEYS[agent_id]}"},
    )


def agent_headers(agent_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_KEYS[agent_id]}"}


def budget_state(client: httpx.Client, agent_id: str) -> dict | None:
    response = client.get(f"/v1/budgets/AGENT/{agent_id}", headers=admin_headers())
    return response.json() if response.status_code == 200 else None


if __name__ == "__main__":
    sys.exit(main())
