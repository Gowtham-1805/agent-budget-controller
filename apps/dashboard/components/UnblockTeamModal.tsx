"use client";

import { useState } from "react";
import { CheckCircleIcon, XIcon, AlertCircleIcon } from "./Icons";
import type { TeamSummary } from "../lib/types";

interface UnblockTeamModalProps {
  team: TeamSummary;
  onClose: () => void;
  onSuccess: () => void;
}

export function UnblockTeamModal({ team, onClose, onSuccess }: UnblockTeamModalProps) {
  const [amount, setAmount] = useState("50.00");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const normalizedWindow =
    team.window_type === "MONTH" || team.window_type === "MONTHLY" ? "MONTHLY"
    : team.window_type === "WEEK" || team.window_type === "WEEKLY" ? "WEEKLY"
    : team.window_type === "DAY" || team.window_type === "DAILY" ? "DAILY"
    : "MONTHLY";

  const parsedAmount = parseFloat(amount);
  const isValid = !isNaN(parsedAmount) && parsedAmount > 0;

  async function handleUnblock(e: React.FormEvent) {
    e.preventDefault();
    if (!isValid) {
      setError("Please enter a valid budget amount greater than $0.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/teams/${encodeURIComponent(team.team_id)}/budget`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount_usd: parsedAmount.toFixed(2),
          window: normalizedWindow,
          warning_percent: team.warning_threshold_percent || 80,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || data.detail || "Failed to restore team budget");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to restore team budget");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal-content" style={{ padding: 24, maxWidth: 480 }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Restore Team Budget
            </h3>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
              Target team: <code className="font-mono">{team.team_id}</code>
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

        <div className="notice-box info" style={{ marginBottom: 16 }}>
          <CheckCircleIcon size={16} style={{ flexShrink: 0 }} />
          <div style={{ fontSize: 12.5, lineHeight: 1.4 }}>
            Set a new budget ceiling to re-allow member agents to make API calls. The window type is preserved from the previous configuration.
          </div>
        </div>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            <AlertCircleIcon size={14} />
            {error}
          </div>
        )}

        <form onSubmit={handleUnblock} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
              New Budget Ceiling (USD) <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            <input
              type="number"
              min="0.01"
              step="0.01"
              className="form-input font-mono"
              placeholder="50.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
              autoFocus
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              Window: <strong>{normalizedWindow}</strong> &nbsp;·&nbsp; Warning threshold: <strong>{team.warning_threshold_percent || 80}%</strong>
            </div>
          </div>

          {/* Summary row */}
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
              <strong>Budget change:</strong> $0.00 &rarr; ${isValid ? parsedAmount.toFixed(2) : "—"}
            </span>
            <span className={`badge ${isValid ? "badge-ok" : "badge-neutral"}`}>
              {isValid ? "Active" : "Enter amount"}
            </span>
          </div>

          {/* Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="btn btn-outline" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading || !isValid}>
              {loading ? "Restoring..." : "Restore Budget"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
