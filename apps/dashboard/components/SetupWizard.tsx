"use client";

import { useState } from "react";
import type { CatalogModel, ProviderConfig } from "../lib/types";
import { ArrowRightIcon, CheckCircleIcon } from "./Icons";

interface SetupWizardProps {
  providers: ProviderConfig[];
  catalogModels: CatalogModel[];
  onComplete?: () => void;
}

export function SetupWizard({
  providers,
  catalogModels,
  onComplete,
}: SetupWizardProps) {
  const [step, setStep] = useState(1);
  const [selectedProvider, setSelectedProvider] = useState<string>("bedrock");
  const [apiKey, setApiKey] = useState("");
  const [region, setRegion] = useState("us-east-1");
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [testResult, setTestResult] = useState<{
    status: "idle" | "testing" | "success" | "error";
    message?: string;
  }>({ status: "idle" });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const currentProviderObj = providers.find((p) => p.provider === selectedProvider);
  const models = catalogModels.filter((m) => m.provider === selectedProvider);

  function handleSelectProvider(prov: string) {
    setSelectedProvider(prov);
    const m = catalogModels.filter((model) => model.provider === prov);
    const first = m[0];
    if (first) {
      setSelectedModel(first.model);
    }
    setTestResult({ status: "idle" });
    setError(null);
  }

  async function handleSaveAndTest() {
    setLoading(true);
    setError(null);
    setTestResult({ status: "testing" });
    try {
      const updates: any = {
        default_model: selectedModel || currentProviderObj?.default_model,
      };
      if (selectedProvider === "openai" || selectedProvider === "anthropic") {
        if (!apiKey.trim()) {
          throw new Error("Please enter a valid API key for " + selectedProvider);
        }
        updates.api_key = apiKey.trim();
      } else if (selectedProvider === "bedrock") {
        updates.region = region;
      }

      const saveRes = await fetch(`/api/providers/${selectedProvider}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!saveRes.ok) {
        const err = await saveRes.json();
        throw new Error(err.detail || err.error || "Failed to save provider config");
      }

      const testRes = await fetch(`/api/providers/${selectedProvider}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedModel }),
      });
      const resData = await testRes.json();
      if (resData.status === "healthy") {
        setTestResult({
          status: "success",
          message: resData.message || "Connection validated successfully.",
        });
        setStep(4);
      } else {
        setTestResult({
          status: "error",
          message: resData.message || "Connection check failed.",
        });
      }
    } catch (err: any) {
      setError(err.message || "Operation failed");
      setTestResult({ status: "error", message: err.message });
    } finally {
      setLoading(false);
    }
  }

  async function handleEnableAndComplete() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/providers/${selectedProvider}/enable`, {
        method: "POST",
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || err.error || "Failed to enable provider");
      }
      setStep(6);
      setTimeout(() => {
        onComplete?.();
      }, 1200);
    } catch (err: any) {
      setError(err.message || "Failed to enable provider");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 28, background: "var(--surface-raised)" }}>
      <div className="card-header">
        <div>
          <h3 className="card-title">Connect Production LLM Provider</h3>
          <div style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 2 }}>
            Set up and verify your real inference provider before routing agent traffic.
          </div>
        </div>
        <span className="badge info">First-Run Setup</span>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 14 }}>
          {error}
        </div>
      )}

      {/* Step 1: Select Provider */}
      {step === 1 && (
        <div>
          <div style={{ marginBottom: 14, fontSize: 12.5, color: "var(--text-secondary)" }}>
            Choose a provider for production inference:
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginBottom: 20 }}>
            <div
              className="card"
              style={{
                cursor: "pointer",
                borderColor: selectedProvider === "bedrock" ? "var(--primary)" : "var(--border)",
                background: selectedProvider === "bedrock" ? "var(--surface-active)" : "var(--surface)",
              }}
              onClick={() => handleSelectProvider("bedrock")}
            >
              <h4 style={{ margin: "0 0 4px 0", fontSize: 13.5 }}>Amazon Bedrock</h4>
              <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                Recommended for AWS ECS deployments via Task IAM role.
              </div>
            </div>

            <div
              className="card"
              style={{
                cursor: "pointer",
                borderColor: selectedProvider === "openai" ? "var(--primary)" : "var(--border)",
                background: selectedProvider === "openai" ? "var(--surface-active)" : "var(--surface)",
              }}
              onClick={() => handleSelectProvider("openai")}
            >
              <h4 style={{ margin: "0 0 4px 0", fontSize: 13.5 }}>OpenAI</h4>
              <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                Standard OpenAI API key. Masked &amp; encrypted at rest.
              </div>
            </div>

            <div
              className="card"
              style={{
                cursor: "pointer",
                borderColor: selectedProvider === "anthropic" ? "var(--primary)" : "var(--border)",
                background: selectedProvider === "anthropic" ? "var(--surface-active)" : "var(--surface)",
              }}
              onClick={() => handleSelectProvider("anthropic")}
            >
              <h4 style={{ margin: "0 0 4px 0", fontSize: 13.5 }}>Anthropic</h4>
              <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                Anthropic API key with preflight token counting endpoint.
              </div>
            </div>
          </div>

          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setStep(2)}
          >
            <span>Continue with {selectedProvider.toUpperCase()}</span>
            <ArrowRightIcon size={11} />
          </button>
        </div>
      )}

      {/* Step 2 & 3: Configuration & Connection Test */}
      {(step === 2 || step === 3) && (
        <div>
          <h4 style={{ margin: "0 0 12px 0", fontSize: 13.5 }}>Configure {selectedProvider.toUpperCase()}</h4>

          {selectedProvider === "bedrock" ? (
            <div className="form-group" style={{ maxWidth: 400 }}>
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
              <div className="notice-box info" style={{ marginTop: 10, fontSize: 11.5 }}>
                Production AWS authentication uses the ECS task IAM role. No AWS secret keys required.
              </div>
            </div>
          ) : (
            <div className="form-group" style={{ maxWidth: 460 }}>
              <label className="form-label">API Key</label>
              <input
                type="password"
                className="input mono"
                placeholder={selectedProvider === "openai" ? "sk-..." : "anthropic-..."}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <div className="form-hint">
                Secrets are masked and never returned to the browser in raw format.
              </div>
            </div>
          )}

          {testResult.message && (
            <div
              className={`notice-box ${testResult.status === "error" ? "danger" : "info"}`}
              style={{ maxWidth: 460, marginTop: 10 }}
            >
              {testResult.message}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setStep(1)}
              disabled={loading}
            >
              Back
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleSaveAndTest}
              disabled={loading}
            >
              {loading ? "Testing Connection..." : "Test Connection & Continue"}
            </button>
          </div>
        </div>
      )}

      {/* Step 4 & 5: Model Selection & Pricing Confirmation */}
      {(step === 4 || step === 5) && (
        <div>
          <h4 style={{ margin: "0 0 12px 0", fontSize: 13.5 }}>Default Model &amp; Price Confirmation</h4>
          <div className="form-group" style={{ maxWidth: 460 }}>
            <label className="form-label">Default Model</label>
            <select
              className="input mono"
              value={selectedModel || (models[0]?.model ?? "")}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              {models.map((m) => (
                <option key={m.model} value={m.model}>
                  {m.model} &mdash; ${m.input_per_million}/M input, ${m.output_per_million}/M output
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setStep(2)}
              disabled={loading}
            >
              Back
            </button>
            <button
              type="button"
              className="btn btn-success btn-sm"
              onClick={handleEnableAndComplete}
              disabled={loading}
            >
              {loading ? "Enabling..." : "Enable Provider & Finish"}
            </button>
          </div>
        </div>
      )}

      {/* Step 6: Complete */}
      {step === 6 && (
        <div style={{ textAlign: "center", padding: "16px 0" }}>
          <span className="badge ok" style={{ fontSize: 13, padding: "4px 12px" }}>
            <CheckCircleIcon size={14} />
            <span>Provider Successfully Enabled</span>
          </span>
          <p style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 8 }}>
            {selectedProvider.toUpperCase()} is now active for governed inference requests.
          </p>
        </div>
      )}
    </div>
  );
}
