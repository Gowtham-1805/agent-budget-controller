"use client";

import { useState } from "react";
import type { CatalogModel, ProviderConfig } from "../lib/types";
import { ArrowRightIcon, CheckCircleIcon, ShieldIcon, CpuIcon, AlertCircleIcon, SettingsIcon } from "./Icons";

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
  const [selectedProvider, setSelectedProvider] = useState<string>("openai");
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
          throw new Error("Please enter a valid API key for " + selectedProvider.toUpperCase());
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
        setStep(3);
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
      setStep(4);
      setTimeout(() => {
        onComplete?.();
      }, 1200);
    } catch (err: any) {
      setError(err.message || "Failed to enable provider");
    } finally {
      setLoading(false);
    }
  }

  const stepLabels = {
    1: "Step 1 of 3: Choose Provider",
    2: "Step 2 of 3: Configure Credentials",
    3: "Step 3 of 3: Confirm Default Model",
    4: "Setup Completed",
  };

  return (
    <div
      className="shadcn-card"
      style={{
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 18,
        border: "1px solid var(--brand-blue-border)",
        backgroundColor: "#ffffff",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Connect Production LLM Provider
            </h3>
            <span className="badge badge-cyan" style={{ fontSize: 11 }}>
              First-Run Setup
            </span>
          </div>
          <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
            Set up and verify your real inference credentials before routing autonomous agent traffic.
          </p>
        </div>

        {/* Clear Descriptive Step Pill */}
        <div>
          <span
            className={`badge ${step === 4 ? "badge-ok" : "badge-indigo"}`}
            style={{ fontSize: 11.5, padding: "4px 12px", fontWeight: 600 }}
          >
            {stepLabels[step as keyof typeof stepLabels]}
          </span>
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* Step 1: Select Provider */}
      {step === 1 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
            Select an LLM provider to configure:
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 14 }}>
            <div
              className="shadcn-card"
              style={{
                cursor: "pointer",
                padding: 16,
                borderColor: selectedProvider === "openai" ? "var(--primary)" : "var(--border-app)",
                backgroundColor: selectedProvider === "openai" ? "var(--bg-app)" : "#ffffff",
                boxShadow: selectedProvider === "openai" ? "var(--shadow-md)" : "var(--shadow-card)",
                transition: "all 0.15s ease",
              }}
              onClick={() => handleSelectProvider("openai")}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <h4 style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>OpenAI</h4>
                {selectedProvider === "openai" && <span className="badge badge-indigo">Selected</span>}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                Standard OpenAI API key (<code>gpt-4o</code>, <code>gpt-4o-mini</code>, <code>o1</code>). Masked at rest.
              </div>
            </div>

            <div
              className="shadcn-card"
              style={{
                cursor: "pointer",
                padding: 16,
                borderColor: selectedProvider === "anthropic" ? "var(--primary)" : "var(--border-app)",
                backgroundColor: selectedProvider === "anthropic" ? "var(--bg-app)" : "#ffffff",
                boxShadow: selectedProvider === "anthropic" ? "var(--shadow-md)" : "var(--shadow-card)",
                transition: "all 0.15s ease",
              }}
              onClick={() => handleSelectProvider("anthropic")}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <h4 style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>Anthropic</h4>
                {selectedProvider === "anthropic" && <span className="badge badge-indigo">Selected</span>}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                Claude 3.5 Sonnet &amp; Haiku API keys with native preflight token counting endpoint.
              </div>
            </div>

            <div
              className="shadcn-card"
              style={{
                cursor: "pointer",
                padding: 16,
                borderColor: selectedProvider === "bedrock" ? "var(--primary)" : "var(--border-app)",
                backgroundColor: selectedProvider === "bedrock" ? "var(--bg-app)" : "#ffffff",
                boxShadow: selectedProvider === "bedrock" ? "var(--shadow-md)" : "var(--shadow-card)",
                transition: "all 0.15s ease",
              }}
              onClick={() => handleSelectProvider("bedrock")}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <h4 style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>Amazon Bedrock</h4>
                {selectedProvider === "bedrock" && <span className="badge badge-indigo">Selected</span>}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                AWS ECS Task IAM Role authentication without static API keys.
              </div>
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setStep(2)}
              style={{ padding: "8px 16px" }}
            >
              <span>Continue with {selectedProvider.toUpperCase()}</span>
              <ArrowRightIcon size={12} />
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Configuration & Handshake Test */}
      {step === 2 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
              Configure Credentials for {selectedProvider.toUpperCase()}
            </div>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
              Provide connection parameters to perform an authorization check.
            </p>
          </div>

          {selectedProvider === "bedrock" ? (
            <div style={{ maxWidth: 480 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 6 }}>
                AWS Region
              </label>
              <select
                className="form-select font-mono"
                value={region}
                onChange={(e) => setRegion(e.target.value)}
              >
                <option value="us-east-1">us-east-1 (N. Virginia)</option>
                <option value="us-east-2">us-east-2 (Ohio)</option>
                <option value="us-west-2">us-west-2 (Oregon)</option>
                <option value="eu-central-1">eu-central-1 (Frankfurt)</option>
                <option value="ap-northeast-1">ap-northeast-1 (Tokyo)</option>
              </select>
              <div className="notice-box info" style={{ marginTop: 10 }}>
                AWS IAM Task Role credentials are used automatically in production ECS deployments.
              </div>
            </div>
          ) : (
            <div style={{ maxWidth: 480, display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                API Secret Key <span style={{ color: "var(--danger)" }}>*</span>
              </label>
              <input
                type="password"
                className="form-input font-mono"
                placeholder={selectedProvider === "openai" ? "sk-proj-..." : "sk-ant-..."}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoFocus
                style={{ padding: "8px 12px", fontSize: 13 }}
              />
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Secrets are encrypted at rest and never transmitted to the browser in raw format.
              </div>
            </div>
          )}

          {testResult.message && (
            <div
              className={`notice-box ${testResult.status === "error" ? "danger" : "info"}`}
              style={{ maxWidth: 480 }}
            >
              {testResult.message}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button
              type="button"
              className="btn btn-outline"
              onClick={() => setStep(1)}
              disabled={loading}
            >
              Back
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSaveAndTest}
              disabled={loading}
            >
              {loading ? "Testing Handshake..." : "Test Connection & Continue"}
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Default Model & Final Activation */}
      {step === 3 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
              Default Model &amp; Rate Confirmation
            </div>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
              Select the initial default model for {selectedProvider.toUpperCase()} workloads.
            </p>
          </div>

          <div style={{ maxWidth: 480 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 6 }}>
              Default Model
            </label>
            <select
              className="form-select font-mono"
              value={selectedModel || (models[0]?.model ?? "")}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              {models.map((m) => (
                <option key={m.model} value={m.model}>
                  {m.model} (${m.input_per_million}/M in, ${m.output_per_million}/M out)
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button
              type="button"
              className="btn btn-outline"
              onClick={() => setStep(2)}
              disabled={loading}
            >
              Back
            </button>
            <button
              type="button"
              className="btn btn-primary"
              style={{ backgroundColor: "var(--ok)", borderColor: "var(--ok)" }}
              onClick={handleEnableAndComplete}
              disabled={loading}
            >
              {loading ? "Activating..." : "Enable Provider & Finish"}
            </button>
          </div>
        </div>
      )}

      {/* Step 4: Success State */}
      {step === 4 && (
        <div style={{ textAlign: "center", padding: "20px 0" }}>
          <span className="badge badge-ok" style={{ fontSize: 13, padding: "6px 14px" }}>
            <CheckCircleIcon size={15} />
            <span>Provider Successfully Configured &amp; Active</span>
          </span>
          <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 8 }}>
            {selectedProvider.toUpperCase()} is now active for governed inference requests.
          </p>
        </div>
      )}
    </div>
  );
}
