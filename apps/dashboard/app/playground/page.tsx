"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AlertCircleIcon, CheckCircleIcon, CpuIcon, PlayIcon, ShieldIcon } from "../../components/Icons";
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

  useEffect(() => {
    fetch("/api/agents")
      .then((res) => res.json())
      .then((data: AgentSummary[]) => {
        if (Array.isArray(data) && data.length > 0) {
          setAgents(data);
          const first = data[0];
          if (!selectedAgent && first) {
            setSelectedAgent(first.agent_id);
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

      const data = (await res.json()) as PlaygroundRunResponse;
      setResult(data);
    } catch (err: any) {
      setError(err.message || "Failed to execute governed request");
    } finally {
      setRunning(false);
    }
  }

  return (
    <main>
      <div className="page-header">
        <div>
          <h1 className="page-title">Governed Inference Playground</h1>
          <p className="page-description">
            Execute live requests through the financial authorization firewall and trace the 10-stage reservation lifecycle in real time.
          </p>
        </div>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, alignItems: "flex-start" }}>
        {/* Left Column: Request Configuration */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Inference Request Parameters</span>
          </div>

          <form onSubmit={handleRun}>
            <div className="form-group">
              <label className="form-label">Governed Agent</label>
              <select
                className="input mono"
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

            {currentAgentObj && (
              <div
                style={{
                  background: "var(--surface-inset)",
                  padding: "8px 12px",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-subtle)",
                  fontSize: 11.5,
                  marginBottom: 14,
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 6,
                }}
              >
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Available Balance:</span>{" "}
                  <span className="money" style={{ color: "var(--ok)", fontWeight: 600 }}>
                    {usd(currentAgentObj.available_usd, 4)}
                  </span>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Agent Status:</span>{" "}
                  <span className={`badge ${currentAgentObj.status === "ACTIVE" ? "ok" : "danger"}`} style={{ fontSize: 10 }}>
                    {currentAgentObj.status}
                  </span>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Preferred:</span>{" "}
                  <code className="font-mono">{currentAgentObj.preferred_model}</code>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Fallback:</span>{" "}
                  <code className="font-mono">{currentAgentObj.fallback_models?.[0] || "None"}</code>
                </div>
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div className="form-group">
                <label className="form-label">Model Override (Optional)</label>
                <select
                  className="input mono"
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

              <div className="form-group">
                <label className="form-label">Max Output Tokens</label>
                <input
                  type="number"
                  className="input mono"
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(Number(e.target.value))}
                />
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Session ID (Optional)</label>
              <input
                type="text"
                className="input mono"
                placeholder="Leave blank or e.g. ses_eval_01"
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Prompt Content</label>
              <textarea
                className="input mono"
                rows={4}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                required
              />
            </div>

            <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() =>
                  setPrompt("Calculate Fibonacci sequence of 50 and explain each step in full detail.")
                }
              >
                High Token Prompt
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() =>
                  setPrompt("Reply with the single word: ACK")
                }
              >
                Micro Token Prompt
              </button>
            </div>

            <div>
              <button
                type="submit"
                className="btn btn-primary"
                style={{ width: "100%", padding: "8px 14px" }}
                disabled={running || agents.length === 0}
              >
                <PlayIcon size={13} />
                <span>{running ? "Authorizing & Dispatching..." : "Execute Governed Inference"}</span>
              </button>
            </div>
          </form>
        </div>

        {/* Right Column: Results & 10-Stage Trace */}
        <div>
          {!result && !running && (
            <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
              <div style={{ color: "var(--primary-text)", marginBottom: 8 }}>
                <ShieldIcon size={32} />
              </div>
              <div className="card-title">Inference Awaiting Authorization</div>
              <p style={{ color: "var(--text-secondary)", fontSize: 12.5, maxWidth: 340, margin: "6px auto 0" }}>
                Select an agent and click &quot;Execute Governed Inference&quot; to observe preflight token bounding, atomic reservation, and cost settlement.
              </p>
            </div>
          )}

          {running && (
            <div className="card" style={{ textAlign: "center", padding: "48px 24px" }}>
              <div className="pulse-dot" style={{ margin: "0 auto 12px", width: 10, height: 10 }} />
              <div className="card-title">Authorizing &amp; Dispatching...</div>
              <p style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 4 }}>
                Counting preflight tokens &rarr; Locking worst-case budget reservation &rarr; Performing inference.
              </p>
            </div>
          )}

          {result && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {/* Decision Banner */}
              <div
                className="card"
                style={{
                  borderLeft: result.blocked
                    ? "3px solid var(--danger)"
                    : result.substituted
                    ? "3px solid var(--info)"
                    : "3px solid var(--ok)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <div className="card-subtitle">Firewall Decision</div>
                    <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2, display: "flex", alignItems: "center", gap: 8 }}>
                      <span>{result.decision}</span>
                      <span className={`badge ${result.blocked ? "danger" : "ok"}`}>
                        {result.status}
                      </span>
                    </div>
                  </div>

                  <div style={{ textAlign: "right" }}>
                    <div className="card-subtitle">Provider Calls</div>
                    <div className="money" style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>
                      {result.provider_calls_made} {result.provider_calls_made === 1 ? "Dispatched" : "Calls (0 Spend)"}
                    </div>
                  </div>
                </div>

                {result.substituted && (
                  <div className="notice-box info" style={{ marginTop: 10, marginBottom: 0, padding: "6px 10px", fontSize: 12 }}>
                    <strong>Model Substituted:</strong> Requested <code>{result.requested_model}</code> &rarr; routed to economy candidate <code>{result.effective_model}</code> ({result.routing_reason || "BUDGET_PRESSURE"}).
                  </div>
                )}

                {result.blocked && (
                  <div className="notice-box danger" style={{ marginTop: 10, marginBottom: 0, padding: "6px 10px", fontSize: 12 }}>
                    <strong>Interception:</strong> Request blocked before reaching provider. Reason: {result.block_reason}
                  </div>
                )}
              </div>

              {/* Financial & Token Breakdown Strip */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="card">
                  <div className="card-subtitle">Token Metering</div>
                  <div style={{ fontSize: 12, marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                    <div>Input: <strong>{result.actual_input_tokens || result.preflight_input_tokens}</strong></div>
                    <div>Output: <strong>{result.actual_output_tokens}</strong> (cap: {result.reserved_output_tokens})</div>
                    <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: 4, fontWeight: 600 }}>
                      Total: {result.total_tokens} tokens
                    </div>
                  </div>
                </div>

                <div className="card">
                  <div className="card-subtitle">Financial Settlement</div>
                  <div style={{ fontSize: 12, marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
                    <div>Worst-Case Hold: <span className="money">{usd(result.estimated_cost_usd, 6)}</span></div>
                    <div>Actual Cost: <span className="money" style={{ color: "var(--ok)", fontWeight: 600 }}>{usd(result.actual_cost_usd, 6)}</span></div>
                    {result.estimated_savings_usd && (
                      <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: 4, color: "var(--info)" }}>
                        Credited Back: {usd(result.estimated_savings_usd, 6)}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Response Output Box */}
              <div className="card">
                <div className="card-subtitle" style={{ marginBottom: 8 }}>Inference Response</div>
                <div
                  style={{
                    background: "var(--surface-inset)",
                    padding: "10px 12px",
                    borderRadius: "var(--radius-sm)",
                    fontFamily: result.blocked ? "var(--font-sans)" : "var(--font-mono)",
                    fontSize: 12.5,
                    whiteSpace: "pre-wrap",
                    color: result.blocked ? "var(--danger)" : "var(--text-primary)",
                    maxHeight: 180,
                    overflowY: "auto",
                    border: "1px solid var(--border-subtle)",
                  }}
                >
                  {result.response_text}
                </div>
              </div>

              {/* 10-Stage Lifecycle Stepper */}
              <div className="card">
                <LifecycleStepper steps={result.lifecycle_steps} />
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

export default function PlaygroundPage() {
  return (
    <Suspense fallback={<div className="empty-state">Loading Playground...</div>}>
      <PlaygroundContent />
    </Suspense>
  );
}
