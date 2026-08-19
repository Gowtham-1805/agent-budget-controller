"use client";

import { useState } from "react";
import { XIcon } from "./Icons";

interface CreateTeamModalProps {
  onClose: () => void;
  onSuccess: () => void;
}

export function CreateTeamModal({ onClose, onSuccess }: CreateTeamModalProps) {
  const [teamId, setTeamId] = useState("");
  const [amountUsd, setAmountUsd] = useState("50.00");
  const [window, setWindow] = useState("MONTHLY");
  const [warningPercent, setWarningPercent] = useState(80);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!teamId.trim()) {
      setError("Team identifier is required.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/teams", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_id: teamId.trim(),
          budget: {
            amount_usd: amountUsd,
            window,
            warning_percent: Number(warningPercent),
          },
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to create team");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to create team");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Provision Engineering Team</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <XIcon size={14} />
          </button>
        </div>

        <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginBottom: 16 }}>
          Establish an overarching team budget ceiling. Member agents draw from their own limits, bounded by this team cap.
        </p>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Team Identifier</label>
            <input
              type="text"
              className="input mono"
              placeholder="e.g. engineering, platform, search"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              required
              autoFocus
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="form-group">
              <label className="form-label">Budget Limit (USD)</label>
              <input
                type="text"
                className="input mono"
                placeholder="50.00"
                value={amountUsd}
                onChange={(e) => setAmountUsd(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label">Budget Window</label>
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
            <div className="form-hint">
              Fires a durable one-time warning alert when total committed spend crosses this threshold.
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? "Provisioning..." : "Provision Team"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
