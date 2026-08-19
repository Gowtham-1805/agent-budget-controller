"use client";

import { useEffect, useState } from "react";
import type { AgentSummary, CatalogModel } from "../lib/types";
import { XIcon } from "./Icons";

interface EditModelPolicyModalProps {
  agent: AgentSummary;
  onClose: () => void;
  onSuccess: () => void;
}

export function EditModelPolicyModal({
  agent,
  onClose,
  onSuccess,
}: EditModelPolicyModalProps) {
  const [provider, setProvider] = useState("openai");
  const [preferredModel, setPreferredModel] = useState(agent.preferred_model || "gpt-4o-mini");
  const [fallbackModel, setFallbackModel] = useState(
    agent.fallback_models?.[0] || agent.preferred_model || "gpt-4o-mini",
  );
  const [allowFallback, setAllowFallback] = useState(agent.substitution_enabled);

  const [models, setModels] = useState<CatalogModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/catalog/models")
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setModels(data);
          const found = data.find((m) => m.model === agent.preferred_model);
          if (found) setProvider(found.provider);
        }
      })
      .catch(() => {});
  }, [agent.preferred_model]);

  const providerModels = models.filter(
    (m) => m.provider.toLowerCase() === provider.toLowerCase(),
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/agents/${encodeURIComponent(agent.agent_id)}/routing`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider,
          preferred_model: preferredModel,
          fallback_models: fallbackModel && fallbackModel !== preferredModel ? [fallbackModel] : [],
          allow_fallback: allowFallback,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to update routing policy");
      }
      onSuccess();
    } catch (err: any) {
      setError(err.message || "Failed to update routing policy");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal-content">
        <div className="modal-header">
          <div className="modal-title">Model Policy &bull; {agent.agent_id}</div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <XIcon size={14} />
          </button>
        </div>

        <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginBottom: 16 }}>
          Configure model substitution and economy fallback policies under budget pressure.
        </p>

        {error && (
          <div className="notice-box danger" style={{ marginBottom: 14 }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Provider</label>
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
                    {m.model} (${m.input_per_million}/M)
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
                    {m.model} (${m.input_per_million}/M)
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div
            style={{
              padding: "10px 12px",
              background: "var(--surface-inset)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--border-subtle)",
              marginTop: 10,
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
            }}
          >
            <input
              type="checkbox"
              id="allowFallback"
              checked={allowFallback}
              onChange={(e) => setAllowFallback(e.target.checked)}
              style={{ marginTop: 2 }}
            />
            <label htmlFor="allowFallback" style={{ cursor: "pointer", fontSize: 12, color: "var(--text-secondary)" }}>
              Enable automatic model substitution under budget pressure (routes to cheaper verified fallback instead of blocking immediately).
            </label>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? "Saving..." : "Save Policy"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
