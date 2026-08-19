"use client";

import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { AlertCircleIcon, XIcon, LockIcon } from "./Icons";

interface PauseAgentModalProps {
  agent: AgentSummary;
  onClose: () => void;
  onSuccess: () => void;
}

export function PauseAgentModal({
  agent,
  onClose,
  onSuccess,
}: PauseAgentModalProps) {
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handlePause(e: React.FormEvent) {
    e.preventDefault();
    if (!reason.trim()) {
      setError("Please provide a reason for pausing this agent.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agent.agent_id)}/pause`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to pause agent");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to pause agent");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal-content" style={{ padding: 24, maxWidth: 500 }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                Administrative Intervention Pause
              </h3>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
              Target agent: <code className="font-mono">{agent.agent_id}</code>
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
            Pausing immediately locks all new inference requests with <code>HTTP 423 Locked</code>. No tokens will reach any provider.
          </div>
        </div>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handlePause} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
              Justification for Administrative Pause <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            <input
              type="text"
              className="form-input font-mono"
              placeholder="e.g. Investigating prompt recursion in agent scheduler loop"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              autoFocus
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              Required for compliance: recorded permanently in the append-only audit ledger.
            </div>
          </div>

          {/* Modal Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="btn btn-outline" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-danger" disabled={loading}>
              {loading ? "Pausing Agent..." : "Confirm Pause"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
