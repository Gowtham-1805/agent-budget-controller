"use client";

import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { XIcon } from "./Icons";

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
  const [amountUsd, setAmountUsd] = useState(agent.limit_usd);
  const [window, setWindow] = useState(agent.window_type || "MONTHLY");
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
      const res = await fetch(`/api/agents/${encodeURIComponent(agent.agent_id)}/budget`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount_usd: amountUsd,
          window,
          warning_percent: Number(warningPercent),
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to update agent budget");
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
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Edit Agent Budget &bull; {agent.agent_id}</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <XIcon size={14} />
          </button>
        </div>

        <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginBottom: 16 }}>
          Adjust the periodic spend limit for this agent. Financial invariants are calculated atomically.
        </p>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="form-group">
              <label className="form-label">New Limit (USD)</label>
              <input
                type="text"
                className="input mono"
                value={amountUsd}
                onChange={(e) => setAmountUsd(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label">Window</label>
              <select
                className="input mono"
                value={window}
                onChange={(e) => setWindow(e.target.value)}
              >
                <option value="MONTHLY">MONTHLY</option>
                <option value="WEEKLY">WEEKLY</option>
                <option value="DAILY">DAILY</option>
              </select>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Warning Threshold (%)</label>
            <input
              type="number"
              className="input mono"
              min={1}
              max={99}
              value={warningPercent}
              onChange={(e) => setWarningPercent(Number(e.target.value))}
            />
          </div>

          <div
            style={{
              padding: "8px 12px",
              background: "var(--surface-inset)",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-subtle)",
              fontSize: 12,
              color: "var(--text-secondary)",
              marginTop: 10,
            }}
          >
            <strong>Adjustment:</strong> ${agent.limit_usd} &rarr; ${amountUsd} (
            {pctChange >= 0 ? `+${pctChange}%` : `${pctChange}%`})
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? "Saving..." : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
