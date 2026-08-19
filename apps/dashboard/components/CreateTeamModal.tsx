"use client";

import { useState } from "react";
import { XIcon, UsersIcon } from "./Icons";

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
      <div className="modal-content" style={{ padding: 24, maxWidth: 520 }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Provision Engineering Team
            </h3>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
              Establish an overarching budget ceiling for member agents.
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
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
              Team Identifier <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            <input
              type="text"
              className="form-input font-mono"
              placeholder="e.g. platform-infra, search-team, data-science"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              required
              autoFocus
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Periodic Ceiling (USD)
              </label>
              <input
                type="text"
                className="form-input font-mono"
                placeholder="50.00"
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
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              Fires a durable one-time warning alert when total committed spend crosses this threshold.
            </div>
          </div>

          {/* Modal Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="btn btn-outline" onClick={onClose} disabled={loading}>
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
