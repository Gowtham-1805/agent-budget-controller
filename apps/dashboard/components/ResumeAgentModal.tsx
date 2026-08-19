"use client";

import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { CheckCircleIcon, XIcon, ShieldIcon } from "./Icons";

interface ResumeAgentModalProps {
  agent: AgentSummary;
  onClose: () => void;
  onSuccess: () => void;
}

export function ResumeAgentModal({
  agent,
  onClose,
  onSuccess,
}: ResumeAgentModalProps) {
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleResume(e: React.FormEvent) {
    e.preventDefault();
    if (!reason.trim()) {
      setError("A valid justification is mandatory to restore an agent to service.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agent.agent_id)}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to resume agent");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to resume agent");
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
                Restore Agent to Service
              </h3>
              <span className="badge badge-neutral font-mono" style={{ fontSize: 11 }}>
                {agent.agent_id}
              </span>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
              Re-enables preflight authorization against remaining budget.
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

        {agent.pause_reason && (
          <div
            style={{
              padding: "10px 12px",
              backgroundColor: "var(--bg-app)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--border-app)",
              marginBottom: 14,
              fontSize: 12,
              color: "var(--text-secondary)",
            }}
          >
            <strong style={{ color: "var(--warning)" }}>Prior Pause Reason:</strong> {agent.pause_reason}
          </div>
        )}

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleResume} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
              Root Cause Justification / Resolution Summary <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            <input
              type="text"
              className="form-input font-mono"
              placeholder="e.g. Verified agent prompt loop resolved in patch v1.4"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              autoFocus
            />
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              Mandatory audit requirement: resuming without recording why is refused by the gateway.
            </div>
          </div>

          {/* Modal Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="btn btn-outline" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              style={{ backgroundColor: "var(--ok)", borderColor: "var(--ok)" }}
              disabled={loading}
            >
              {loading ? "Restoring..." : "Restore Service"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
