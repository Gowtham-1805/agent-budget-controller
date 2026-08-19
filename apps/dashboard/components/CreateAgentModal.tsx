"use client";

import { useEffect, useState } from "react";
import type { CatalogModel, TeamSummary } from "../lib/types";
import { XIcon, CpuIcon } from "./Icons";

interface CreateAgentModalProps {
  initialTeamId?: string;
  onClose: () => void;
  onSuccess: () => void;
}

export function CreateAgentModal({
  initialTeamId = "",
  onClose,
  onSuccess,
}: CreateAgentModalProps) {
  const [agentId, setAgentId] = useState("");
  const [teamId, setTeamId] = useState(initialTeamId);
  const [amountUsd, setAmountUsd] = useState("10.00");
  const [window, setWindow] = useState("MONTHLY");
  const [warningPercent, setWarningPercent] = useState(80);
  const [provider, setProvider] = useState("test");
  const [preferredModel, setPreferredModel] = useState("premium");
  const [fallbackModel, setFallbackModel] = useState("cheap");
  const [sessionBudgetUsd, setSessionBudgetUsd] = useState("2.00");
  const [maxOutputTokens, setMaxOutputTokens] = useState(1000);

  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [models, setModels] = useState<CatalogModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/teams")
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          setTeams(data);
          if (!teamId && data.length > 0 && data[0]) {
            setTeamId(data[0].team_id);
          }
        }
      })
      .catch(() => {});

    fetch("/api/catalog/models")
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setModels(data);
          const first = data[0];
          if (first) {
            setProvider(first.provider);
            setPreferredModel(first.model);
            setFallbackModel(data[1]?.model || first.model);
          }
        }
      })
      .catch(() => {});
  }, [teamId]);

  const providerModels = models.filter(
    (m) => m.provider.toLowerCase() === provider.toLowerCase(),
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!agentId.trim()) {
      setError("Agent identifier is required.");
      return;
    }
    if (!teamId.trim()) {
      setError("Please select or specify a Team ID.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const payload = {
        agent_id: agentId.trim(),
        team_id: teamId.trim(),
        budget: {
          amount_usd: amountUsd,
          window,
          warning_percent: Number(warningPercent),
        },
        routing: {
          provider,
          preferred_model: preferredModel,
          fallback_models: fallbackModel && fallbackModel !== preferredModel ? [fallbackModel] : [],
        },
        session_budget_usd: sessionBudgetUsd ? sessionBudgetUsd : undefined,
        default_max_output_tokens: Number(maxOutputTokens),
      };

      const res = await fetch("/api/agents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to provision agent");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to provision agent");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal-content" style={{ padding: 24, maxWidth: 540 }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Provision Governed Agent
            </h3>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
              Register an autonomous agent with periodic spend limits and model routing.
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
          {/* Identity: Agent ID & Team */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Agent Identifier <span style={{ color: "var(--danger)" }}>*</span>
              </label>
              <input
                type="text"
                className="form-input font-mono"
                placeholder="e.g. data-analyzer-01"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                required
                autoFocus
              />
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Parent Team <span style={{ color: "var(--danger)" }}>*</span>
              </label>
              <select
                className="form-select font-mono"
                value={teamId}
                onChange={(e) => setTeamId(e.target.value)}
                required
              >
                {teams.map((t) => (
                  <option key={t.team_id} value={t.team_id}>
                    {t.team_id} (Ceiling: ${t.limit_usd})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Budget Limits & Window */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Periodic Budget (USD)
              </label>
              <input
                type="text"
                className="form-input font-mono"
                placeholder="10.00"
                value={amountUsd}
                onChange={(e) => setAmountUsd(e.target.value)}
                required
              />
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Reset Window
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

          {/* Model Routing */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Preferred Model
              </label>
              <select
                className="form-select font-mono"
                value={preferredModel}
                onChange={(e) => setPreferredModel(e.target.value)}
              >
                {providerModels.map((m) => (
                  <option key={m.model} value={m.model}>
                    {m.model} (${m.input_per_million}/M)
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Fallback Route (Economy)
              </label>
              <select
                className="form-select font-mono"
                value={fallbackModel}
                onChange={(e) => setFallbackModel(e.target.value)}
              >
                {providerModels.map((m) => (
                  <option key={m.model} value={m.model}>
                    {m.model} (${m.input_per_million}/M)
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Session Budget & Output Ceiling */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Session Budget Cap (USD)
              </label>
              <input
                type="text"
                className="form-input font-mono"
                placeholder="2.00"
                value={sessionBudgetUsd}
                onChange={(e) => setSessionBudgetUsd(e.target.value)}
              />
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
                Max Output Tokens / Call
              </label>
              <input
                type="number"
                className="form-input font-mono"
                min={1}
                max={8192}
                value={maxOutputTokens}
                onChange={(e) => setMaxOutputTokens(Number(e.target.value))}
              />
            </div>
          </div>

          {/* Modal Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="btn btn-outline" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? "Provisioning..." : "Provision Agent"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
