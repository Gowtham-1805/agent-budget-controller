"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import {
  AlertCircleIcon,
  CheckCircleIcon,
  CpuIcon,
  PlayIcon,
  ShieldIcon,
  TerminalIcon,
  ActivityIcon,
  SlidersIcon,
  ClockIcon,
} from "../../components/Icons";
import { LifecycleStepper } from "../../components/LifecycleStepper";
import { usd } from "../../lib/api";
import type { AgentSummary, CatalogModel, PlaygroundRunResponse } from "../../lib/types";

function PlaygroundContent() {
  const searchParams = useSearchParams();
  const initialAgent = searchParams ? searchParams.get("agent") || "" : "";

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [models, setModels] = useState<CatalogModel[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>(initialAgent);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [maxTokens, setMaxTokens] = useState<number>(500);
  const [sessionId, setSessionId] = useState<string>("");
  const [prompt, setPrompt] = useState<string>(
    "Explain how atomic financial reservation prevents autonomous AI agents from exceeding allocated budgets.",
  );

  const [running, setRunning] = useState<boolean>(false);
  const [result, setResult] = useState<PlaygroundRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetch("/api/agents")
      .then((res) => res.json())
      .then((data: AgentSummary[]) => {
        if (Array.isArray(data) && data.length > 0) {
          setAgents(data);
          if (!selectedAgent && data[0]) {
            setSelectedAgent(data[0].agent_id);
          }
        }
      })
      .catch(() => {});

    fetch("/api/catalog/models")
      .then((res) => res.json())
      .then((data: CatalogModel[]) => {
        if (Array.isArray(data)) {
          setModels(data);
        }
      })
      .catch(() => {});
  }, [selectedAgent]);

  const currentAgentObj = agents.find((a) => a.agent_id === selectedAgent);

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedAgent) {
      setError("Please select a governed agent.");
      return;
    }
    if (!prompt.trim()) {
      setError("Please enter a prompt.");
      return;
    }

    setRunning(true);
    setError(null);
    setResult(null);

    try {
      const res = await fetch("/api/playground/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: selectedAgent,
          prompt: prompt.trim(),
          session_id: sessionId.trim() ? sessionId.trim() : undefined,
          model: selectedModel || undefined,
          max_output_tokens: Number(maxTokens),
        }),
      });

      const data = await res.json().catch(() => null);
      if (data && data.lifecycle_steps) {
        setResult(data as PlaygroundRunResponse);
      } else if (!res.ok) {
        throw new Error(data?.error || `Gateway returned HTTP status ${res.status}`);
      } else if (data) {
        setResult(data as PlaygroundRunResponse);
      }
    } catch (err: any) {
      setError(err.message || "Failed to execute governed request");
    } finally {
      setRunning(false);
    }
  }

  function handleCopyResponse() {
    if (!result?.response_text) return;
    navigator.clipboard.writeText(result.response_text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Header Banner */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">Governed Inference Playground</h1>
          <p className="page-description">
            Execute live requests through the financial authorization firewall and trace the 10-stage reservation lifecycle in real time.
          </p>
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* Main 2-Column Workbench Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.3fr", gap: 20, alignItems: "flex-start" }}>
        {/* ------------------------------------------------------------------ */}
        {/* Left Column: Request Configuration & Parameters                    */}
        {/* ------------------------------------------------------------------ */}
        <div className="shadcn-card" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card-header" style={{ marginBottom: 0 }}>
            <div>
              <div className="card-title">Inference Parameters</div>
              <div className="card-subtitle">Configure agent scope, token bounds &amp; payload</div>
            </div>
            <span className="badge badge-neutral" style={{ fontSize: 11 }}>
              <SlidersIcon size={12} /> Workbench
            </span>
          </div>

          <form onSubmit={handleRun} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {/* 1. Governed Agent Select */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 6 }}>
                Governed Agent
              </label>
              <select
                className="form-select font-mono"
                value={selectedAgent}
                onChange={(e) => {
                  setSelectedAgent(e.target.value);
                  const a = agents.find((ag) => ag.agent_id === e.target.value);
                  if (a) setSelectedModel(a.preferred_model);
                }}
                required
              >
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>
                    {a.agent_id} (Team: {a.team_id}, Spend: ${a.committed_usd}/${a.limit_usd}, {a.status})
                  </option>
                ))}
              </select>
            </div>

            {/* Agent Live Context Banner */}
            {currentAgentObj && (
              <div
                style={{
                  backgroundColor: "var(--bg-app)",
                  padding: "10px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--border-app)",
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 8,
                  fontSize: 12,
                }}
              >
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Available: </span>
                  <strong className="money" style={{ color: "var(--ok)" }}>
                    {usd(currentAgentObj.available_usd, 4)}
                  </strong>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Status: </span>
                  <span className={`badge ${currentAgentObj.status === "ACTIVE" ? "badge-ok" : "badge-danger"}`} style={{ fontSize: 10 }}>
                    {currentAgentObj.status}
                  </span>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Preferred: </span>
                  <code style={{ fontSize: 11, backgroundColor: "var(--bg-muted)", padding: "1px 5px", borderRadius: 4 }}>
                    {currentAgentObj.preferred_model}
                  </code>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Fallback: </span>
                  <code style={{ fontSize: 11, backgroundColor: "var(--bg-muted)", padding: "1px 5px", borderRadius: 4 }}>
                    {currentAgentObj.fallback_models?.[0] || "None"}
                  </code>
                </div>
              </div>
            )}

            {/* 2. Model Override & Max Output Tokens */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 6 }}>
                  Model Override (Optional)
                </label>
                <select
                  className="form-select font-mono"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                >
                  <option value="">Agent Default ({currentAgentObj?.preferred_model || "Default"})</option>
                  {models.map((m) => (
                    <option key={m.model} value={m.model}>
                      {m.model} (${m.input_per_million}/M)
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 6 }}>
                  Max Output Tokens
                </label>
                <input
                  type="number"
                  className="form-input font-mono"
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(Number(e.target.value))}
                  min={1}
                  max={8192}
                />
              </div>
            </div>

            {/* 3. Session ID */}
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 6 }}>
                Session ID (Optional)
              </label>
              <input
                type="text"
                className="form-input font-mono"
                placeholder="Leave blank or e.g. ses_eval_01"
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
              />
            </div>

            {/* 4. Prompt Input & Quick Preset Buttons */}
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                  Prompt Content
                </label>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {prompt.length} chars
                </span>
              </div>
              <textarea
                className="form-textarea font-mono"
                rows={4}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                required
              />
            </div>

            {/* Quick Prompt Presets */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() =>
                  setPrompt("Explain how atomic financial reservation prevents autonomous AI agents from exceeding allocated budgets.")
                }
              >
                Atomic Reservation
              </button>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() =>
                  setPrompt("Generate a 10-paragraph essay on recursive autonomous agent systems and financial circuit breakers.")
                }
              >
                High Token Simulation
              </button>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => setPrompt("Reply with ACK.")}
              >
                Micro Token ACK
              </button>
            </div>

            {/* Primary Action Button */}
            <button
              type="submit"
              className="btn btn-primary"
              style={{ width: "100%", padding: "10px 16px", marginTop: 6, fontWeight: 600 }}
              disabled={running || agents.length === 0}
            >
              <PlayIcon size={14} />
              <span>{running ? "Authorizing & Dispatching Request..." : "Execute Governed Inference"}</span>
            </button>
          </form>
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* Right Column: Execution Trace & Financial Settlement               */}
        {/* ------------------------------------------------------------------ */}
        <div>
          {!result && !running && (
            <div className="shadcn-card" style={{ textAlign: "center", padding: "56px 24px" }}>
              <div
                style={{
                  width: 48,
                  height: 48,
                  borderRadius: "9999px",
                  backgroundColor: "var(--brand-blue-soft)",
                  color: "var(--brand-blue)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  margin: "0 auto 14px",
                }}
              >
                <ShieldIcon size={24} />
              </div>
              <div className="card-title" style={{ fontSize: 16 }}>
                Awaiting Governed Execution
              </div>
              <p style={{ color: "var(--text-secondary)", fontSize: 12.5, maxWidth: 360, margin: "8px auto 0" }}>
                Select an agent and prompt on the left to observe preflight input metering, worst-case budget locking, model routing, and actual cost reconciliation.
              </p>
            </div>
          )}

          {running && (
            <div className="shadcn-card" style={{ textAlign: "center", padding: "56px 24px" }}>
              <div className="pulse-dot" style={{ margin: "0 auto 14px", width: 12, height: 12 }} />
              <div className="card-title" style={{ fontSize: 16 }}>
                Evaluating Financial Firewall...
              </div>
              <p style={{ color: "var(--text-secondary)", fontSize: 12.5, maxWidth: 360, margin: "8px auto 0" }}>
                Counting preflight tokens &rarr; Locking worst-case budget reservation &rarr; Dispatching inference.
              </p>
            </div>
          )}

          {result && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {/* 1. Decision Banner */}
              <div
                className="shadcn-card"
                style={{
                  borderLeft: result.blocked
                    ? "4px solid var(--danger)"
                    : result.substituted
                    ? "4px solid var(--brand-blue)"
                    : "4px solid var(--ok)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <div className="card-subtitle">Firewall Authorization Decision</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                      <span style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                        {result.decision}
                      </span>
                      <span className={`badge ${result.blocked ? "badge-danger" : "badge-ok"}`}>
                        {result.status}
                      </span>
                    </div>
                  </div>

                  <div style={{ textAlign: "right" }}>
                    <div className="card-subtitle">Provider Calls</div>
                    <div className="money" style={{ fontSize: 14, fontWeight: 700, marginTop: 4 }}>
                      {result.provider_calls_made} {result.provider_calls_made === 1 ? "Dispatched" : "Calls ($0.00 Spend)"}
                    </div>
                  </div>
                </div>

                {result.substituted && (
                  <div className="notice-box info" style={{ marginTop: 12 }}>
                    <strong>Model Substituted:</strong> Requested <code>{result.requested_model}</code> &rarr; routed to fallback candidate <code>{result.effective_model}</code> ({result.routing_reason || "BUDGET_PRESSURE"}).
                  </div>
                )}

                {result.blocked && (
                  <div className="notice-box danger" style={{ marginTop: 12 }}>
                    <strong>Interception:</strong> Request blocked before reaching LLM provider. Reason: {result.block_reason}
                  </div>
                )}
              </div>

              {/* 2. Financial Settlement & Token Strip (4 KPI Tiles) */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                <div className="shadcn-card" style={{ padding: "12px 14px" }}>
                  <div className="card-subtitle">Total Tokens</div>
                  <div className="money" style={{ fontSize: 16, fontWeight: 700, marginTop: 2 }}>
                    {result.total_tokens}
                  </div>
                  <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 2 }}>
                    {result.actual_input_tokens || result.preflight_input_tokens} in / {result.actual_output_tokens} out
                  </div>
                </div>

                <div className="shadcn-card" style={{ padding: "12px 14px" }}>
                  <div className="card-subtitle">Worst-Case Hold</div>
                  <div className="money" style={{ fontSize: 16, fontWeight: 700, marginTop: 2 }}>
                    {usd(result.estimated_cost_usd, 4)}
                  </div>
                  <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 2 }}>
                    Reserved before provider
                  </div>
                </div>

                <div className="shadcn-card" style={{ padding: "12px 14px" }}>
                  <div className="card-subtitle">Actual Cost</div>
                  <div className="money" style={{ fontSize: 16, fontWeight: 700, color: "var(--ok)", marginTop: 2 }}>
                    {usd(result.actual_cost_usd, 4)}
                  </div>
                  <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 2 }}>
                    Settled to ledger
                  </div>
                </div>

                <div className="shadcn-card" style={{ padding: "12px 14px" }}>
                  <div className="card-subtitle">Credited Back</div>
                  <div className="money" style={{ fontSize: 16, fontWeight: 700, color: "var(--brand-blue)", marginTop: 2 }}>
                    {usd(result.estimated_savings_usd || "0.0000", 4)}
                  </div>
                  <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 2 }}>
                    Returned to budget
                  </div>
                </div>
              </div>

              {/* 3. Inference Response Console */}
              <div className="shadcn-card" style={{ padding: "14px 16px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div className="card-subtitle">Inference Output</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="badge badge-neutral" style={{ fontSize: 10.5 }}>
                      {result.effective_model}
                    </span>
                    <button
                      type="button"
                      onClick={handleCopyResponse}
                      className="btn btn-outline btn-sm"
                      style={{ fontSize: 11, padding: "2px 8px" }}
                    >
                      {copied ? "Copied!" : "Copy"}
                    </button>
                  </div>
                </div>
                <pre
                  style={{
                    backgroundColor: "var(--bg-app)",
                    padding: "12px 14px",
                    borderRadius: "var(--radius-md)",
                    fontFamily: result.blocked ? "var(--font-sans)" : "var(--font-mono)",
                    fontSize: 12.5,
                    whiteSpace: "pre-wrap",
                    color: result.blocked ? "var(--danger)" : "var(--text-primary)",
                    maxHeight: 180,
                    overflowY: "auto",
                    border: "1px solid var(--border-app)",
                  }}
                >
                  {result.response_text}
                </pre>
              </div>

              {/* 4. 10-Stage Lifecycle Stepper */}
              <div className="shadcn-card">
                <LifecycleStepper steps={result.lifecycle_steps} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PlaygroundPage() {
  return (
    <Suspense fallback={<div className="shadcn-card" style={{ textAlign: "center", padding: 48 }}>Loading playground...</div>}>
      <PlaygroundContent />
    </Suspense>
  );
}
