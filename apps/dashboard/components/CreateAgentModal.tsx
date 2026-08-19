"use client";

import { useEffect, useState } from "react";
import type { CatalogModel, TeamSummary } from "../lib/types";
import { XIcon } from "./Icons";

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
  const [provider, setProvider] = useState("openai");
  const [preferredModel, setPreferredModel] = useState("gpt-4o-mini");
  const [fallbackModel, setFallbackModel] = useState("gpt-4o-mini");
  const [sessionBudgetUsd, setSessionBudgetUsd] = useState("2.00");
  const [maxOutputTokens, setMaxOutputTokens] = useState(4096);

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
          if (!teamId && data.length > 0) {
            const first = data[0];
            if (first) setTeamId(first.team_id);
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
            setFallbackModel(first.model);
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
      <div className="modal-content" style={{ maxWidth: 520 }}>
        <div className="modal-header">
          <div className="modal-title">Provision Governed Agent</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <XIcon size={14} />
          </button>
        </div>

        <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginBottom: 16 }}>
          Register an autonomous AI agent with bounded periodic spend, token ceilings, and fallback routing.
        </p>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="form-group">
              <label className="form-label">Agent Identifier</label>
              <input
                type="text"
                className="input mono"
                placeholder="e.g. code-review-agent"
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                required
                autoFocus
              />
            </div>

            <div className="form-group">
              <label className="form-label">Team Assignment</label>
              {teams.length > 0 ? (
                <select
                  className="input mono"
                  value={teamId}
                  onChange={(e) => setTeamId(e.target.value)}
                  required
                >
                  {teams.map((t) => (
                    <option key={t.team_id} value={t.team_id}>
                      {t.team_id} (${t.limit_usd} cap)
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  className="input mono"
                  placeholder="e.g. engineering"
                  value={teamId}
                  onChange={(e) => setTeamId(e.target.value)}
                  required
                />
              )}
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="form-group">
              <label className="form-label">Monthly Limit (USD)</label>
              <input
                type="text"
                className="input mono"
                placeholder="10.00"
                value={amountUsd}
                onChange={(e) => setAmountUsd(e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label">Session Limit (USD)</label>
              <input
                type="text"
                className="input mono"
                placeholder="2.00"
                value={sessionBudgetUsd}
                onChange={(e) => setSessionBudgetUsd(e.target.value)}
              />
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="form-group">
              <label className="form-label">LLM Provider</label>
              <select
                className="input mono"
                value={provider}
                onChange={(e) => {
                  setProvider(e.target.value);
                  const provM = models.filter((m) => m.provider.toLowerCase() === e.target.value.toLowerCase());
                  const first = provM[0];
                  if (first) {
                    setPreferredModel(first.model);
                    setFallbackModel(first.model);
                  }
                }}
              >
                <option value="bedrock">Amazon Bedrock</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="test">Test Provider</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Max Output Tokens</label>
              <input
                type="number"
                className="input mono"
                value={maxOutputTokens}
                onChange={(e) => setMaxOutputTokens(Number(e.target.value))}
              />
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="form-group">
              <label className="form-label">Preferred Model</label>
              <select
                className="input mono"
                value={preferredModel}
                onChange={(e) => setPreferredModel(e.target.value)}
              >
                {providerModels.map((m) => (
                  <option key={m.model} value={m.model}>
                    {m.model}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Fallback (Economy)</label>
              <select
                className="input mono"
                value={fallbackModel}
                onChange={(e) => setFallbackModel(e.target.value)}
              >
                {providerModels.map((m) => (
                  <option key={m.model} value={m.model}>
                    {m.model}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={loading}>
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
