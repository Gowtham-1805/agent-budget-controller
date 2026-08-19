"use client";

import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { XIcon, SlidersIcon, ShieldIcon } from "./Icons";

interface EditAgentBudgetModalProps {
  agent: AgentSummary;
  onClose: () => void;
  onSuccess: () => void;
}

export function EditAgentBudgetModal({
  agent,
  onClose,
  onSuccess,
}: EditAgentBudgetModalProps) {
  const initialWindow =
    agent.window_type === "MONTH" || agent.window_type === "MONTHLY"
      ? "MONTHLY"
      : agent.window_type === "WEEK" || agent.window_type === "WEEKLY"
      ? "WEEKLY"
      : agent.window_type === "DAY" || agent.window_type === "DAILY"
      ? "DAILY"
      : "MONTHLY";

  const [amountUsd, setAmountUsd] = useState(agent.limit_usd);
  const [window, setWindow] = useState(initialWindow);
  const [warningPercent, setWarningPercent] = useState(80);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const prevLimit = Number(agent.limit_usd) || 1;
  const newLimit = Number(amountUsd) || 0;
  const pctChange = Math.round(((newLimit - prevLimit) / prevLimit) * 100);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const normalizedWindow =
        window === "MONTH" || window === "MONTHLY"
          ? "MONTHLY"
          : window === "WEEK" || window === "WEEKLY"
          ? "WEEKLY"
          : "DAILY";

      const res = await fetch(`/api/agents/${encodeURIComponent(agent.agent_id)}/budget`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount_usd: amountUsd,
          window: normalizedWindow,
          warning_percent: Number(warningPercent),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || data.detail || "Failed to update agent budget");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to update agent budget");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal-content" style={{ padding: 24 }}>
        {/* Modal Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Edit Agent Budget &bull; <code className="font-mono">{agent.agent_id}</code>
            </h3>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 2 }}>
              Adjust the periodic ceiling. Invariants are evaluated atomically.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            style={{ padding: 6 }}
          >
            <XIcon size={16} />
          </button>
        </div>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                New Budget Cap (USD)
              </label>
              <input
                type="text"
                className="form-input font-mono"
                value={amountUsd}
                onChange={(e) => setAmountUsd(e.target.value)}
                required
              />
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Reset Window
              </label>
              <select
                className="form-select font-mono"
                value={window}
                onChange={(e) => setWindow(e.target.value)}
              >
                <option value="MONTHLY">MONTHLY</option>
                <option value="WEEKLY">WEEKLY</option>
                <option value="DAILY">DAILY</option>
              </select>
            </div>
          </div>

          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
              Warning Alert Threshold (%)
            </label>
            <input
              type="number"
              className="form-input font-mono"
              min={1}
              max={99}
              value={warningPercent}
              onChange={(e) => setWarningPercent(Number(e.target.value))}
            />
          </div>

          <div
            style={{
              padding: "10px 12px",
              backgroundColor: "var(--bg-app)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--border-app)",
              fontSize: 12,
              color: "var(--text-secondary)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>
              <strong>Adjustment:</strong> ${agent.limit_usd} &rarr; ${amountUsd}
            </span>
            <span className={`badge ${pctChange > 0 ? "badge-indigo" : pctChange < 0 ? "badge-warning" : "badge-neutral"}`}>
              {pctChange >= 0 ? `+${pctChange}%` : `${pctChange}%`}
            </span>
          </div>

          {/* Modal Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button
              type="button"
              className="btn btn-outline"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading}
            >
              {loading ? "Saving Changes..." : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
