"use client";

import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { AlertCircleIcon, XIcon } from "./Icons";

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
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Pause Agent &bull; {agent.agent_id}</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <XIcon size={14} />
          </button>
        </div>

        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          <AlertCircleIcon size={16} style={{ color: "var(--danger)", flexShrink: 0 }} />
          <div>
            Pausing immediately locks all new inference calls (returning <code>HTTP 423 Locked</code>) before any tokens reach an LLM provider.
          </div>
        </div>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handlePause}>
          <div className="form-group">
            <label className="form-label">
              Justification for Administrative Pause <span style={{ color: "var(--danger)" }}>*</span>
            </label>
            <input
              type="text"
              className="input"
              placeholder="e.g. Investigating unexpected recursion in scheduler loop"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              autoFocus
            />
            <div className="form-hint">
              Recorded immutably to the audit ledger.
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-danger" disabled={loading}>
              {loading ? "Pausing..." : "Confirm Pause"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
