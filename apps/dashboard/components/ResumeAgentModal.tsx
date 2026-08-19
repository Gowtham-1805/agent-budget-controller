"use client";

import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { CheckCircleIcon, XIcon } from "./Icons";

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
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Restore Agent &bull; {agent.agent_id}</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <XIcon size={14} />
          </button>
        </div>

        <div className="notice-box info" style={{ marginBottom: 14 }}>
          <CheckCircleIcon size={16} style={{ color: "var(--info)", flexShrink: 0 }} />
          <div>
            Restoring service re-enables preflight authorization against remaining budget.
          </div>
        </div>

        {agent.pause_reason && (
          <div
            style={{
              padding: "8px 12px",
              background: "var(--surface-inset)",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-subtle)",
              marginBottom: 14,
              fontSize: 12,
              color: "var(--text-secondary)",
            }}
          >
            <strong style={{ color: "var(--warn)" }}>Intervention Reason:</strong> {agent.pause_reason}
          </div>
        )}

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleResume}>
          <div className="form-group">
            <label className="form-label">
              Review Justification / Root Cause Resolution <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            <input
              type="text"
              className="input"
              placeholder="e.g. Verified agent prompt loop resolved in patch v1.4"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              autoFocus
            />
            <div className="form-hint">
              Mandatory audit requirement: resuming without recording why is prohibited.
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-success" disabled={loading}>
              {loading ? "Restoring..." : "Restore Service"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
