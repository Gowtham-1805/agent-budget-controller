"use client";

import { useEffect, useState } from "react";
import type { AgentSummary, CatalogModel } from "../lib/types";
import { XIcon, SlidersIcon, ShieldIcon } from "./Icons";

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
  const [provider, setProvider] = useState("test");
  const [preferredModel, setPreferredModel] = useState(agent.preferred_model || "premium");
  const [fallbackModel, setFallbackModel] = useState(
    agent.fallback_models?.[0] || agent.preferred_model || "cheap",
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
          if (found) {
            setProvider(found.provider);
          }
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
      <div className="modal-content" style={{ padding: 24, maxWidth: 540 }}>
        {/* Modal Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                Model Policy &amp; Degradation
              </h3>
              <span className="badge badge-neutral font-mono" style={{ fontSize: 11 }}>
                {agent.agent_id}
              </span>
            </div>
            <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
              Configure model substitution and economy fallback policies under budget pressure.
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
          {/* Provider Select */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 4 }}>
              Provider
            </label>
            <select
              className="form-select font-mono"
              value={provider}
              onChange={(e) => {
                const newProv = e.target.value;
                setProvider(newProv);
                const provM = models.filter((m) => m.provider.toLowerCase() === newProv.toLowerCase());
                if (provM.length > 0) {
                  setPreferredModel(provM[0]?.model || "");
                  setFallbackModel(provM[provM.length > 1 ? 1 : 0]?.model || provM[0]?.model || "");
                }
              }}
            >
              <option value="test">Test Provider (Deterministic Doubles)</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="bedrock">Amazon Bedrock</option>
            </select>
          </div>

          {/* Preferred vs Fallback Model */}
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
                Fallback (Economy)
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

          {/* Automatic Substitution Checkbox Card */}
          <div
            style={{
              padding: "12px 14px",
              backgroundColor: allowFallback ? "var(--brand-blue-soft)" : "var(--bg-app)",
              borderRadius: "var(--radius-md)",
              border: allowFallback ? "1px solid var(--brand-blue-border)" : "1px solid var(--border-app)",
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
            onClick={() => setAllowFallback(!allowFallback)}
          >
            <input
              type="checkbox"
              id="allowFallback"
              checked={allowFallback}
              onChange={(e) => setAllowFallback(e.target.checked)}
              style={{ marginTop: 2, accentColor: "var(--primary)", cursor: "pointer" }}
              onClick={(e) => e.stopPropagation()}
            />
            <label
              htmlFor="allowFallback"
              style={{ cursor: "pointer", fontSize: 12.5, color: "var(--text-primary)", lineHeight: 1.4 }}
            >
              <strong>Enable automatic model substitution under budget pressure</strong>
              <div style={{ color: "var(--text-secondary)", fontSize: 11.5, marginTop: 2 }}>
                Routes to cheaper verified fallback candidate instead of returning an immediate HTTP 429 block when preferred model exceeds available funds.
              </div>
            </label>
          </div>

          {/* Modal Footer */}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button
              type="button"
              className="btn btn-outline"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading}
            >
              {loading ? "Saving Policy..." : "Save Policy"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
