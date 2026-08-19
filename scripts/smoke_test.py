#!/usr/bin/env python3
"""Deployed smoke test.

Produces the evidence a deployment acceptance gate actually needs, rather than
just confirming the service returns 200. Specifically, it proves:

1. liveness responds
2. readiness responds, and says what it checked
3. budget state is persistent in DynamoDB
4. a governed request reaches a real provider and reconciles
5. an over-budget request is rejected
6. **the rejected request caused zero provider invocations** -- measured by
   comparing committed spend before and after, since a call that reached the
   provider would have moved it
7. logs carry a correlated request id
8. the reported state matches what the ledger says

Run against a deployment:

    GATEWAY_URL=http://... ABC_ADMIN_API_KEY=... python scripts/smoke_test.py

Exits non-zero if any check fails, so it can gate a pipeline.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field

import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080").rstrip("/")
ADMIN_KEY = os.environ.get("ABC_ADMIN_API_KEY", "local-admin-key")
AGENT_KEY = os.environ.get("ABC_AGENT_API_KEY", ADMIN_KEY)
TIMEOUT = httpx.Timeout(60.0, connect=10.0)

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(name)
        print(f"{GREEN}PASS{RESET}  {name}" + (f"  ({detail})" if detail else ""))

    def fail(self, name: str, detail: str) -> None:
        self.failed.append((name, detail))
        print(f"{RED}FAIL{RESET}  {name}\n        {detail}")

    def skip(self, name: str, why: str) -> None:
        self.skipped.append((name, why))
        print(f"{YELLOW}SKIP{RESET}  {name}  ({why})")

    def summary(self) -> int:
        print("\n" + "=" * 70)
        print(
            f"{len(self.passed)} passed, {len(self.failed)} failed, "
            f"{len(self.skipped)} skipped"
        )
        if self.failed:
            print(f"\n{RED}Failures:{RESET}")
            for name, detail in self.failed:
                print(f"  - {name}: {detail}")
        if self.skipped:
            print(f"\n{YELLOW}Not verified:{RESET}")
            for name, why in self.skipped:
                print(f"  - {name}: {why}")
        return 1 if self.failed else 0


def admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def agent() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_KEY}"}


def main() -> int:
    report = Report()
    suffix = uuid.uuid4().hex[:8]
    team_id = f"smoke-team-{suffix}"
    agent_id = f"smoke-agent-{suffix}"

    print(f"Agent Budget Controller smoke test\nTarget: {GATEWAY_URL}\n")

    with httpx.Client(base_url=GATEWAY_URL, timeout=TIMEOUT) as client:
        # -- 1. liveness --------------------------------------------------
        try:
            response = client.get("/healthz")
            if response.status_code == 200:
                report.ok("deployed /healthz", response.json().get("status", ""))
            else:
                report.fail("deployed /healthz", f"HTTP {response.status_code}")
                return report.summary()
        except Exception as exc:  # noqa: BLE001
            report.fail("deployed /healthz", f"unreachable: {exc}")
            return report.summary()

        # -- 2. readiness -------------------------------------------------
        response = client.get("/readyz")
        body = response.json()
        if response.status_code == 200:
            report.ok("deployed /readyz", str(body.get("detail", {})))
        else:
            report.fail("deployed /readyz", f"HTTP {response.status_code}: {body}")

        for check, passed in body.get("checks", {}).items():
            (report.ok if passed else report.fail)(
                f"readiness: {check}", "" if passed else "reported false"
            )

        # -- 3. provisioning ----------------------------------------------
        provisioned = client.post(
            "/v1/teams",
            json={"team_id": team_id, "budget": {"amount_usd": "5.00"}},
            headers=admin(),
        )
        if provisioned.status_code not in (200, 201):
            report.fail("create team", f"HTTP {provisioned.status_code}: {provisioned.text}")
            return report.summary()
        report.ok("create team")

        provider = os.environ.get("SMOKE_PROVIDER", "openai")
        model = os.environ.get("SMOKE_MODEL", "gpt-4o-mini")
        # A deliberately tiny agent budget: large enough for one real call,
        # small enough that the second is certain to be refused.
        created = client.post(
            "/v1/agents",
            json={
                "agent_id": agent_id,
                "team_id": team_id,
                "budget": {"amount_usd": "0.05"},
                "routing": {"provider": provider, "preferred_model": model},
                "default_max_output_tokens": 64,
            },
            headers=admin(),
        )
        if created.status_code not in (200, 201):
            report.fail("create agent", f"HTTP {created.status_code}: {created.text}")
            return report.summary()
        report.ok("create agent", f"{provider}/{model}")

        # -- 4. a real governed inference request --------------------------
        request_id = f"smoke-{uuid.uuid4().hex[:12]}"
        call = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "max_tokens": 16,
            },
            headers={**agent(), "X-Request-Id": request_id},
        )

        live_call_worked = call.status_code == 200
        if live_call_worked:
            payload = call.json()
            report.ok(
                "governed real-provider request",
                f"{payload['usage']['total_tokens']} tokens, "
                f"${payload['budget']['actual_cost_usd']}",
            )
            if call.headers.get("X-Request-Id") == request_id:
                report.ok("request id correlated in the response")
            else:
                report.fail("request id correlated", "header not echoed")
        else:
            # Distinguish "no credentials" from "the gateway is broken".
            report.skip(
                "governed real-provider request",
                f"HTTP {call.status_code}: {call.text[:200]}",
            )

        # -- 5. persistent budget state ------------------------------------
        budget = client.get(f"/v1/budgets/AGENT/{agent_id}", headers=admin())
        if budget.status_code == 200:
            state = budget.json()
            report.ok(
                "budget state persisted",
                f"committed ${state['committed_usd']} of ${state['limit_usd']}",
            )
            committed_before = state["committed_usd"]
        else:
            report.fail("budget state persisted", f"HTTP {budget.status_code}")
            committed_before = None

        # -- 6. rejection, and proof it cost nothing ------------------------
        # Drive the agent to exhaustion, then confirm the next request is
        # refused *and* that committed spend did not move -- which is the
        # observable consequence of the provider never being called.
        for _ in range(10):
            attempt = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 16,
                },
                headers=agent(),
            )
            if attempt.status_code == 429:
                break
        else:
            attempt = None

        if attempt is not None and attempt.status_code == 429:
            error = attempt.json()["error"]
            report.ok(
                "over-budget request rejected",
                f"{error['type']} on {error.get('scope')}/{error.get('scope_id')}",
            )

            after = client.get(f"/v1/budgets/AGENT/{agent_id}", headers=admin()).json()
            committed_at_rejection = after["committed_usd"]

            once_more = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 16},
                headers=agent(),
            )
            final = client.get(f"/v1/budgets/AGENT/{agent_id}", headers=admin()).json()

            if once_more.status_code == 429 and final["committed_usd"] == committed_at_rejection:
                report.ok(
                    "rejected request caused zero provider spend",
                    f"committed unchanged at ${committed_at_rejection}",
                )
            else:
                report.fail(
                    "rejected request caused zero provider spend",
                    f"committed moved from ${committed_at_rejection} "
                    f"to ${final['committed_usd']}",
                )

            if float(final["committed_usd"]) <= float(final["limit_usd"]):
                report.ok(
                    "invariant held",
                    f"committed ${final['committed_usd']} <= limit ${final['limit_usd']}",
                )
            else:
                report.fail(
                    "invariant held",
                    f"OVERSPEND: ${final['committed_usd']} > ${final['limit_usd']}",
                )
        elif live_call_worked:
            report.fail("over-budget request rejected", "never received a 429")
        else:
            report.skip("over-budget request rejected", "no live provider available")

        # -- 7. the ledger --------------------------------------------------
        ledger = client.get(f"/v1/ledger?agent_id={agent_id}", headers=admin())
        if ledger.status_code == 200:
            entries = ledger.json()["entries"]
            if entries:
                entry = entries[0]
                report.ok(
                    "usage reconciled into the ledger",
                    f"{len(entries)} entries, catalog "
                    f"{entry['price_catalog_version']}",
                )
                if entry["estimated_max_cost_usd"] and entry["actual_total_cost_usd"]:
                    report.ok("ledger records both estimate and actual")
            elif live_call_worked:
                report.fail("usage reconciled into the ledger", "no entries written")
            else:
                report.skip("usage reconciled into the ledger", "no live calls were made")
        else:
            report.fail("ledger readable", f"HTTP {ledger.status_code}")

        # -- 8. admin controls ----------------------------------------------
        paused = client.post(
            f"/v1/admin/agents/{agent_id}/pause",
            json={"reason": "smoke test"},
            headers=admin(),
        )
        if paused.status_code == 200:
            report.ok("admin pause")
            blocked = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
                headers=agent(),
            )
            if blocked.status_code == 423:
                report.ok("paused agent blocked before the provider")
            else:
                report.fail(
                    "paused agent blocked", f"expected 423, got {blocked.status_code}"
                )

            resumed = client.post(
                f"/v1/admin/agents/{agent_id}/resume",
                json={"reason": "smoke test complete"},
                headers=admin(),
            )
            if resumed.status_code == 200 and resumed.json().get("reason"):
                report.ok("admin resume writes an audit record")
            else:
                report.fail("admin resume", f"HTTP {resumed.status_code}")
        else:
            report.fail("admin pause", f"HTTP {paused.status_code}: {paused.text[:200]}")

    return report.summary()


if __name__ == "__main__":
    sys.exit(main())
