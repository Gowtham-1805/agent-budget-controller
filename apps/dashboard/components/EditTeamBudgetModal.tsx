"use client";

import { useState } from "react";
import type { TeamSummary } from "../lib/types";
import { XIcon, SlidersIcon, UsersIcon } from "./Icons";

interface EditTeamBudgetModalProps {
  team: TeamSummary;
  onClose: () => void;
  onSuccess: () => void;
}

export function EditTeamBudgetModal({
  team,
  onClose,
  onSuccess,
}: EditTeamBudgetModalProps) {
  const initialWindow =
    team.window_type === "MONTH" || team.window_type === "MONTHLY"
      ? "MONTHLY"
      : team.window_type === "WEEK" || team.window_type === "WEEKLY"
      ? "WEEKLY"
      : team.window_type === "DAY" || team.window_type === "DAILY"
      ? "DAILY"
      : "MONTHLY";

  const [amountUsd, setAmountUsd] = useState(team.limit_usd);
  const [window, setWindow] = useState(initialWindow);
  const [warningPercent, setWarningPercent] = useState(team.warning_threshold_percent || 80);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const prevLimit = Number(team.limit_usd) || 1;
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

      const res = await fetch(`/api/teams/${encodeURIComponent(team.team_id)}/budget`, {
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
        throw new Error(data.error || data.detail || "Failed to update team budget");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to update team budget");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal-content" style={{ padding: 24, maxWidth: 520 }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                Edit Team Budget Ceiling
              </h3>
              <span className="badge badge-neutral font-mono" style={{ fontSize: 11 }}>
                {team.team_id}
              </span>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
              Adjust the periodic ceiling across all member agents in this team.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            style={{ padding: 4, marginTop: -2 }}
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
                New Team Limit (USD)
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
                Budget Window
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
              <strong>Adjustment:</strong> ${team.limit_usd} &rarr; ${amountUsd}
            </span>
            <span className={`badge ${pctChange > 0 ? "badge-indigo" : pctChange < 0 ? "badge-warning" : "badge-neutral"}`}>
              {pctChange >= 0 ? `+${pctChange}%` : `${pctChange}%`}
            </span>
          </div>

          {/* Modal Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="btn btn-outline" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? "Saving..." : "Save Budget"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
