"use client";

import { tokens, usd } from "../lib/format";
import type { BudgetState } from "../lib/types";

export function BudgetCard({ state }: { state: BudgetState }) {
  const committed = clamp(state.utilization_percent);
  const effective = clamp(state.effective_utilization_percent);
  const reservedWidth = Math.max(0, effective - committed);

  const severity =
    state.utilization_percent >= 100
      ? "badge-danger"
      : state.utilization_percent >= 80
      ? "badge-warning"
      : "badge-ok";

  const overspent = state.overage_usd !== "0.000000" && state.overage_usd !== "0";

  return (
    <div className="shadcn-card" style={{ display: "flex", flexDirection: "column", gap: 12, padding: 20 }}>
      {/* Header */}
      <div className="card-header" style={{ marginBottom: 0 }}>
        <div>
          <span className="badge badge-neutral" style={{ fontSize: 10.5, marginBottom: 4, textTransform: "uppercase" }}>
            {state.scope_type}
          </span>
          <h3 style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)" }}>
            {state.scope_id}
          </h3>
        </div>

        <span className={`badge ${severity}`} style={{ fontSize: 11 }}>
          {state.utilization_percent.toFixed(0)}% Utilized
        </span>
      </div>

      {/* Meter */}
      <div>
        <div className="meter-rail" title={`${committed}% committed, ${effective}% effective exposure`}>
          <div
            className={`meter-fill ${state.utilization_percent >= 100 ? "danger" : state.utilization_percent >= 80 ? "warn" : "ok"}`}
            style={{ width: `${committed}%` }}
          />
          <div
            className="meter-flight"
            style={{ left: `${committed}%`, width: `${reservedWidth}%` }}
          />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
          <span>{state.window} window</span>
          <span>{effective > committed ? `${effective.toFixed(1)}% total exposure` : "0 in-flight"}</span>
        </div>
      </div>

      {/* Financial Breakdown */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5, marginTop: 4 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text-muted)" }}>Budget Limit</span>
          <span className="money" style={{ fontWeight: 700 }}>{usd(state.limit_usd, 2)}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text-muted)" }}>Committed Spend</span>
          <span className="money" style={{ color: "var(--text-primary)", fontWeight: 600 }}>{usd(state.committed_usd, 4)}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--cyan)" }}>
            In-Flight Reserved
            {state.open_reservations > 0 && ` (${state.open_reservations} active)`}
          </span>
          <span className="money" style={{ color: "var(--cyan)", fontWeight: 600 }}>{usd(state.reserved_usd, 4)}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text-muted)" }}>Available Balance</span>
          <span className="money" style={{ color: "var(--ok)", fontWeight: 700 }}>{usd(state.available_usd, 4)}</span>
        </div>

        {state.pending_usd !== "0.000000" && state.pending_usd !== "0" && (
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--warning)" }}>Unresolved Pending</span>
            <span className="money" style={{ color: "var(--warning)" }}>{usd(state.pending_usd, 4)}</span>
          </div>
        )}

        {overspent && (
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--danger)" }}>Overage Anomaly</span>
            <span className="money" style={{ color: "var(--danger)" }}>{usd(state.overage_usd, 4)}</span>
          </div>
        )}
      </div>

      {/* Footer Tokens */}
      <div
        style={{
          marginTop: 6,
          borderTop: "1px solid var(--border-app)",
          paddingTop: 8,
          display: "flex",
          justifyContent: "space-between",
          fontSize: 11.5,
        }}
      >
        <span style={{ color: "var(--text-muted)" }}>Tokens (In / Out)</span>
        <span className="money font-mono">
          {tokens(state.input_tokens)} / {tokens(state.output_tokens)}
        </span>
      </div>
    </div>
  );
}

function clamp(percent: number): number {
  return Math.max(0, Math.min(100, percent));
}
