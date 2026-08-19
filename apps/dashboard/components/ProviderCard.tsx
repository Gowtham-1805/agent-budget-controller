"use client";

import { useState } from "react";
import type { CatalogModel, ProviderConfig, ProviderTestResult } from "../lib/types";

interface ProviderCardProps {
  initialConfig: ProviderConfig;
  catalogModels: CatalogModel[];
  onConfigUpdated?: (config: ProviderConfig) => void;
}

export function ProviderCard({
  initialConfig,
  catalogModels,
  onConfigUpdated,
}: ProviderCardProps) {
  const [config, setConfig] = useState<ProviderConfig>(initialConfig);
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState(initialConfig.default_model);
  const [region, setRegion] = useState(initialConfig.region || "us-east-1");
  const [baseUrl, setBaseUrl] = useState(initialConfig.base_url || "");
  const [organizationId, setOrganizationId] = useState(
    initialConfig.organization_id || "",
  );
  const [inputTokens, setInputTokens] = useState(
    initialConfig.test_params?.input_tokens ?? 1000,
  );
  const [outputTokens, setOutputTokens] = useState(
    initialConfig.test_params?.output_tokens ?? 1000,
  );

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [message, setMessage] = useState<{ type: "ok" | "error"; text: string } | null>(null);

  const providerModels = catalogModels.filter(
    (m) => m.provider.toLowerCase() === config.provider.toLowerCase(),
  );

  async function handleSave() {
    setSaving(true);
    setMessage(null);
    try {
      const updates: any = {
        default_model: defaultModel,
      };

      if (config.auth_type === "api_key") {
        if (apiKey.trim()) {
          updates.api_key = apiKey.trim();
        }
        if (baseUrl.trim()) updates.base_url = baseUrl.trim();
        if (organizationId.trim()) updates.organization_id = organizationId.trim();
      } else if (config.auth_type === "iam_role") {
        updates.region = region;
      } else if (config.provider === "test") {
        updates.test_params = {
          input_tokens: Number(inputTokens),
          output_tokens: Number(outputTokens),
        };
      }

      const res = await fetch(`/api/providers/${encodeURIComponent(config.provider)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || err.error || "Failed to save configuration");
      }

      const updated = (await res.json()) as ProviderConfig;
      setConfig(updated);
      setApiKey("");
      setMessage({ type: "ok", text: "Configuration saved successfully." });
      onConfigUpdated?.(updated);
    } catch (err: any) {
      setMessage({ type: "error", text: err.message || "Failed to save configuration" });
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleEnabled() {
    setSaving(true);
    setMessage(null);
    try {
      const endpoint = config.enabled ? "disable" : "enable";
      const res = await fetch(
        `/api/providers/${encodeURIComponent(config.provider)}/${endpoint}`,
        { method: "POST" },
      );

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || err.error || `Failed to ${endpoint} provider`);
      }

      const updated = (await res.json()) as ProviderConfig;
      setConfig(updated);
      setMessage({
        type: "ok",
        text: `Provider ${updated.enabled ? "enabled" : "disabled"} successfully.`,
      });
      onConfigUpdated?.(updated);
    } catch (err: any) {
      setMessage({ type: "error", text: err.message || "Failed to toggle provider state" });
    } finally {
      setSaving(false);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setTestResult(null);
    setMessage(null);
    try {
      const res = await fetch(
        `/api/providers/${encodeURIComponent(config.provider)}/test`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: defaultModel }),
        },
      );

      const result = (await res.json()) as ProviderTestResult;
      setTestResult(result);

      if (result.status === "healthy") {
        setConfig((prev) => ({
          ...prev,
          connection_status: "healthy",
          last_tested_at: result.checked_at,
          last_error: null,
        }));
      } else {
        setConfig((prev) => ({
          ...prev,
          connection_status: "unhealthy",
          last_tested_at: result.checked_at,
          last_error: result.message,
        }));
      }
    } catch (err: any) {
      setMessage({
        type: "error",
        text: `Test request error: ${err.message || "Failed to execute connection test"}`,
      });
    } finally {
      setTesting(false);
    }
  }

  const isTestProvider = config.provider === "test";

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="card-header">
        <div>
          <h3 style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            {config.display_name}
            {isTestProvider && (
              <span className="badge warn" style={{ fontSize: 10 }}>DEV TEST DOUBLE</span>
            )}
          </h3>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            ID: <code className="font-mono">{config.provider}</code>
          </div>
        </div>

        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {config.configured ? (
            <span className="badge ok">Configured</span>
          ) : (
            <span className="badge muted">Not Configured</span>
          )}

          {config.enabled ? (
            <span className="badge info">Enabled</span>
          ) : (
            <span className="badge muted">Disabled</span>
          )}
        </div>
      </div>

      {/* Provider connection status */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "var(--surface-inset)",
          padding: "6px 10px",
          borderRadius: "var(--radius-sm)",
          marginBottom: 12,
          fontSize: 11.5,
          border: "1px solid var(--border-subtle)",
        }}
      >
        <span style={{ color: "var(--text-muted)" }}>Health:</span>
        <span
          className={`badge ${
            config.connection_status === "healthy"
              ? "ok"
              : config.connection_status === "unhealthy"
              ? "danger"
              : "muted"
          }`}
          style={{ fontSize: 10 }}
        >
          {config.connection_status.toUpperCase()}
        </span>
      </div>

      {message && (
        <div
          className={`notice-box ${message.type === "error" ? "danger" : "info"}`}
          style={{ marginBottom: 12, padding: "8px 10px", fontSize: 12 }}
        >
          {message.text}
        </div>
      )}

      {testResult && (
        <div
          className={`notice-box ${testResult.status === "healthy" ? "info" : "danger"}`}
          style={{ marginBottom: 12, padding: "8px 10px", fontSize: 12, flexDirection: "column" }}
        >
          <div>
            <strong>Test ({testResult.status}):</strong> {testResult.message}
          </div>
          <div style={{ fontSize: 10.5, marginTop: 4, color: "var(--text-muted)" }}>
            Checked at {new Date(testResult.checked_at).toLocaleTimeString()} &bull; Auth: {testResult.authentication}
          </div>
        </div>
      )}

      {/* Configuration Form */}
      <div style={{ flex: 1 }}>
        <div className="form-group">
          <label className="form-label">Default Model</label>
          <select
            className="input mono"
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
          >
            {providerModels.map((m) => (
              <option key={m.model} value={m.model}>
                {m.model} (${m.input_per_million}/M in, ${m.output_per_million}/M out)
              </option>
            ))}
          </select>
        </div>

        {/* API Key Authentication */}
        {config.auth_type === "api_key" && (
          <>
            <div className="form-group">
              <label className="form-label">
                API Key {config.masked_api_key && "(Stored & Masked)"}
              </label>
              <input
                type="password"
                className="input mono"
                placeholder={config.masked_api_key || "sk-..."}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
              />
              <div className="form-hint">
                {config.masked_api_key ? (
                  <>
                    Current: <code>{config.masked_api_key}</code>. Leave blank to keep existing.
                  </>
                ) : (
                  "Enter secret key to configure provider."
                )}
              </div>
            </div>

            {config.provider === "openai" && (
              <>
                <div className="form-group">
                  <label className="form-label">Organization ID (Optional)</label>
                  <input
                    type="text"
                    className="input mono"
                    placeholder="org-..."
                    value={organizationId}
                    onChange={(e) => setOrganizationId(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Base URL (Optional)</label>
                  <input
                    type="text"
                    className="input mono"
                    placeholder="https://api.openai.com/v1"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                  />
                </div>
              </>
            )}
          </>
        )}

        {/* Bedrock IAM Authentication */}
        {config.auth_type === "iam_role" && (
          <>
            <div className="form-group">
              <label className="form-label">AWS Region</label>
              <select
                className="input mono"
                value={region}
                onChange={(e) => setRegion(e.target.value)}
              >
                <option value="us-east-1">us-east-1 (N. Virginia)</option>
                <option value="us-east-2">us-east-2 (Ohio)</option>
                <option value="us-west-2">us-west-2 (Oregon)</option>
                <option value="eu-central-1">eu-central-1 (Frankfurt)</option>
                <option value="ap-northeast-1">ap-northeast-1 (Tokyo)</option>
              </select>
            </div>
            <div className="notice-box info" style={{ fontSize: 11.5, marginTop: 8, padding: "8px 10px" }}>
              AWS authentication resolves automatically via ECS Task IAM Role. No static secret keys required.
            </div>
          </>
        )}

        {/* Test Provider Parameters */}
        {isTestProvider && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <div className="form-group">
              <label className="form-label">Synthetic Input Tokens</label>
              <input
                type="number"
                className="input mono"
                value={inputTokens}
                onChange={(e) => setInputTokens(Number(e.target.value))}
              />
            </div>
            <div className="form-group">
              <label className="form-label">Synthetic Output Tokens</label>
              <input
                type="number"
                className="input mono"
                value={outputTokens}
                onChange={(e) => setOutputTokens(Number(e.target.value))}
              />
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div
        style={{
          marginTop: 14,
          paddingTop: 10,
          borderTop: "1px solid var(--border-subtle)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: 6 }}>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleSave}
            disabled={saving || testing}
          >
            {saving ? "Saving..." : "Save"}
          </button>

          <button
            type="button"
            className="btn btn-sm"
            onClick={handleTestConnection}
            disabled={testing || saving || (!config.configured && !apiKey)}
          >
            {testing ? "Testing..." : "Test"}
          </button>
        </div>

        <button
          type="button"
          className={`btn btn-sm ${config.enabled ? "" : "btn-success"}`}
          onClick={handleToggleEnabled}
          disabled={saving || testing || (!config.configured && !config.enabled)}
        >
          {config.enabled ? "Disable" : "Enable"}
        </button>
      </div>
    </div>
  );
}
