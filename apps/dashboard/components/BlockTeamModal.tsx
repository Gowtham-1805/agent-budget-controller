"use client";

import { useState } from "react";
import { AlertCircleIcon, XIcon } from "./Icons";
import type { TeamSummary } from "../lib/types";

interface BlockTeamModalProps {
  team: TeamSummary;
  onClose: () => void;
  onSuccess: () => void;
}

export function BlockTeamModal({ team, onClose, onSuccess }: BlockTeamModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const normalizedWindow =
    team.window_type === "MONTH" || team.window_type === "MONTHLY" ? "MONTHLY"
    : team.window_type === "WEEK" || team.window_type === "WEEKLY" ? "WEEKLY"
    : team.window_type === "DAY" || team.window_type === "DAILY" ? "DAILY"
    : "MONTHLY";

  async function handleBlock(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/teams/${encodeURIComponent(team.team_id)}/budget`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount_usd: "0.00",
          window: normalizedWindow,
          warning_percent: team.warning_threshold_percent || 80,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || data.detail || "Failed to block team");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to block team");
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
              Block Team Budget
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

        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          <AlertCircleIcon size={16} style={{ color: "var(--danger)", flexShrink: 0 }} />
          <div style={{ fontSize: 12.5, lineHeight: 1.4 }}>
            Setting the budget to <code>$0.00</code> immediately halts all member agents — no tokens will reach any provider until the budget is restored.
          </div>
        </div>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleBlock} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
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
              <strong>Budget change:</strong> ${Number(team.limit_usd).toFixed(2)} &rarr; $0.00
            </span>
            <span className="badge badge-danger">Blocked</span>
          </div>

          {/* Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="btn btn-outline" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-danger" disabled={loading}>
              {loading ? "Blocking..." : "Confirm Block"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
