import { tokens, usd } from "../lib/api";
import type { BudgetState } from "../lib/types";

export function BudgetCard({ state }: { state: BudgetState }) {
  const committed = clamp(state.utilization_percent);
  const effective = clamp(state.effective_utilization_percent);
  const reservedWidth = Math.max(0, effective - committed);

  const severity =
    state.utilization_percent >= 100
      ? "danger"
      : state.utilization_percent >= 80
      ? "warn"
      : "";

  const overspent = state.overage_usd !== "0.000000" && state.overage_usd !== "0";

  return (
    <div className="card">
      <div className="card-subtitle">{state.scope_type}</div>
      <h3 style={{ fontSize: 14, marginTop: 2, marginBottom: 8 }}>{state.scope_id}</h3>

      <div className="meter-rail" title={`${committed}% committed, ${effective}% effective exposure`}>
        <div
          className={`meter-fill ${severity}`}
          style={{ width: `${committed}%` }}
        />
        <div
          className="meter-flight"
          style={{ left: `${committed}%`, width: `${reservedWidth}%` }}
        />
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, marginTop: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text-muted)" }}>Limit</span>
          <span className="money" style={{ fontWeight: 600 }}>{usd(state.limit_usd)}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text-muted)" }}>Committed</span>
          <span className="money">{usd(state.committed_usd)}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text-muted)" }}>
            Reserved
            {state.open_reservations > 0 && ` (${state.open_reservations} in flight)`}
          </span>
          <span className="money" style={{ color: "var(--info)" }}>{usd(state.reserved_usd)}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--text-muted)" }}>Available</span>
          <span className="money" style={{ color: "var(--ok)" }}>{usd(state.available_usd)}</span>
        </div>

        {state.pending_usd !== "0.000000" && (
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--warn)" }}>Unresolved Pending</span>
            <span className="money" style={{ color: "var(--warn)" }}>{usd(state.pending_usd)}</span>
          </div>
        )}

        {overspent && (
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--danger)" }}>Overage</span>
            <span className="money" style={{ color: "var(--danger)" }}>{usd(state.overage_usd)}</span>
          </div>
        )}
      </div>

      <div
        style={{
          display: "flex",
          gap: 6,
          marginTop: 12,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <span className={`badge ${severity || "ok"}`}>
          {state.utilization_percent}% used
        </span>
        {effective > committed && (
          <span className="badge info">
            {effective}% effective
          </span>
        )}
        {state.warning_sent && <span className="badge warn">80% warned</span>}
        {overspent && <span className="badge danger">overage</span>}
      </div>

      <div
        style={{
          marginTop: 10,
          borderTop: "1px solid var(--border-subtle)",
          paddingTop: 8,
          display: "flex",
          justifyContent: "space-between",
          fontSize: 11.5,
        }}
      >
        <span style={{ color: "var(--text-muted)" }}>Tokens In / Out</span>
        <span className="money">
          {tokens(state.input_tokens)} / {tokens(state.output_tokens)}
        </span>
      </div>
    </div>
  );
}

function clamp(percent: number): number {
  return Math.max(0, Math.min(100, percent));
}
