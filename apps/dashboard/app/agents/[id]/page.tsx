"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { EditAgentBudgetModal } from "../../../components/EditAgentBudgetModal";
import { EditModelPolicyModal } from "../../../components/EditModelPolicyModal";
import { ChevronRightIcon, PlayIcon, SlidersIcon } from "../../../components/Icons";
import { PauseAgentModal } from "../../../components/PauseAgentModal";
import { ResumeAgentModal } from "../../../components/ResumeAgentModal";
import { tokens, usd } from "../../../lib/api";
import type { AgentSummary, LedgerEntry, SessionView } from "../../../lib/types";

export default function AgentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const agentId = resolvedParams.id;

  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [modal, setModal] = useState<"budget" | "policy" | "pause" | "resume" | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function loadAgentData() {
    setLoading(true);
    Promise.allSettled([
      fetch(`/api/agents`).then((r) => r.json()),
      fetch(`/api/sessions?agent_id=${encodeURIComponent(agentId)}`).then((r) => r.json()),
    ])
      .then(([agentsRes, sessRes]) => {
        if (agentsRes.status === "fulfilled" && Array.isArray(agentsRes.value)) {
          const found = agentsRes.value.find((a: AgentSummary) => a.agent_id === agentId);
          if (found) setAgent(found);
          else setError(`Agent '${agentId}' not found.`);
        }
        if (sessRes.status === "fulfilled" && Array.isArray(sessRes.value)) {
          setSessions(sessRes.value);
        }
      })
      .catch((err) => {
        setError(err.message);
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadAgentData();
  }, [agentId]);

  if (loading) {
    return (
      <main>
        <div className="table-container">
          <div className="empty-state">Loading agent details...</div>
        </div>
      </main>
    );
  }

  if (!agent) {
    return (
      <main>
        <div className="notice-box danger">Agent &apos;{agentId}&apos; could not be found.</div>
        <Link href="/agents" className="btn btn-sm" style={{ marginTop: 12 }}>
          &larr; Back to Agents
        </Link>
      </main>
    );
  }

  const committed = Math.min(100, Math.max(0, agent.utilization_percent));
  const effective = Math.min(100, Math.max(0, agent.effective_utilization_percent));
  const reservedWidth = Math.max(0, effective - committed);

  const isPaused = agent.status.startsWith("PAUSED");
  const isRunaway = agent.status === "PAUSED_RUNAWAY" || agent.review_required;
  const isExhausted = agent.utilization_percent >= 100;
  const isWarning = agent.utilization_percent >= 80 && !isExhausted;

  return (
    <main>
      {/* Breadcrumb */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)", marginBottom: 16 }}>
        <Link href="/agents" style={{ color: "var(--text-secondary)" }}>
          Agents
        </Link>
        <ChevronRightIcon size={12} />
        <span style={{ color: "var(--text-primary)" }}>{agent.agent_id}</span>
      </div>

      {/* Header */}
      <div className="page-header">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h1 className="page-title">{agent.agent_id}</h1>
            {isRunaway ? (
              <span className="badge danger">Runaway Paused</span>
            ) : isPaused ? (
              <span className="badge warn">Paused</span>
            ) : isExhausted ? (
              <span className="badge danger">100% Blocked</span>
            ) : isWarning ? (
              <span className="badge warn">80% Warning</span>
            ) : (
              <span className="badge ok">Active</span>
            )}
          </div>
          <p className="page-description">
            Team: <Link href={`/teams/${agent.team_id}`} style={{ color: "var(--primary-text)" }}>{agent.team_id}</Link> &bull; Window: <strong>{agent.window_type}</strong> &bull; Output Ceiling: <strong>{agent.default_max_output_tokens} tokens/call</strong>
          </p>
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setModal("budget")}
          >
            <SlidersIcon size={12} />
            <span>Edit Budget</span>
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setModal("policy")}
          >
            <span>Model Policy</span>
          </button>
          {isPaused ? (
            <button
              type="button"
              className="btn btn-success btn-sm"
              onClick={() => setModal("resume")}
            >
              Resume Agent
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-danger btn-sm"
              onClick={() => setModal("pause")}
            >
              Pause Agent
            </button>
          )}
          <Link
            href={`/playground?agent=${encodeURIComponent(agent.agent_id)}`}
            className="btn btn-primary btn-sm"
          >
            <PlayIcon size={12} />
            <span>Open in Playground</span>
          </Link>
        </div>
      </div>

      {isPaused && (
        <div className="notice-box danger" style={{ marginBottom: 20 }}>
          <div>
            <strong>Agent is administratively paused:</strong> {agent.pause_reason || "Intervention active."}
            <div style={{ marginTop: 3, fontSize: 12 }}>
              New inference calls are locked at the gateway. Click &quot;Resume Agent&quot; with a justification to restore service.
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Spend Meter & Stats Card */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Budget Consumption &amp; In-Flight Hold</span>
          <div style={{ display: "flex", gap: 6 }}>
            <span className={`badge ${isExhausted ? "danger" : isWarning ? "warn" : "ok"}`}>
              {agent.utilization_percent}% Committed
            </span>
            {effective > committed && (
              <span className="badge info">
                {effective}% Effective Exposure
              </span>
            )}
          </div>
        </div>

        <div className="meter-rail" style={{ height: 6, marginBottom: 16 }}>
          <div
            className={`meter-fill ${isExhausted ? "danger" : isWarning ? "warn" : ""}`}
            style={{ width: `${committed}%` }}
          />
          <div
            className="meter-flight"
            style={{ left: `${committed}%`, width: `${reservedWidth}%` }}
          />
        </div>

        <div className="stats-strip" style={{ border: "none", background: "transparent", margin: 0 }}>
          <div className="stat-cell" style={{ padding: "0 16px 0 0" }}>
            <div className="stat-label">Periodic Cap</div>
            <div className="stat-value money">{usd(agent.limit_usd)}</div>
          </div>

          <div className="stat-cell" style={{ padding: "0 16px" }}>
            <div className="stat-label">Committed Spend</div>
            <div className="stat-value money">{usd(agent.committed_usd, 4)}</div>
          </div>

          <div className="stat-cell" style={{ padding: "0 16px" }}>
            <div className="stat-label">In-Flight (Reserved)</div>
            <div className="stat-value money" style={{ color: "var(--info)" }}>
              {usd(agent.reserved_usd, 4)}
            </div>
          </div>

          <div className="stat-cell" style={{ padding: "0 0 0 16px", borderRight: "none" }}>
            <div className="stat-label">Available Balance</div>
            <div className="stat-value money" style={{ color: "var(--ok)" }}>
              {usd(agent.available_usd, 4)}
            </div>
          </div>
        </div>
      </div>

      {/* Detail Split: Token Volume & Model Policy */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 28 }}>
        <div className="card">
          <div className="card-header">
            <span className="card-title">Token Accounting</span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 12.5 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Cumulative Input Tokens</span>
              <span className="money">{tokens(agent.input_tokens)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Cumulative Output Tokens</span>
              <span className="money">{tokens(agent.output_tokens)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid var(--border-subtle)", paddingTop: 6 }}>
              <span style={{ color: "var(--text-muted)" }}>Total Token Volume</span>
              <span className="money" style={{ fontWeight: 600 }}>
                {tokens(agent.input_tokens + agent.output_tokens)}
              </span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <span className="card-title">Model Policy &amp; Fallbacks</span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 12.5 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Preferred Model</span>
              <code className="font-mono">{agent.preferred_model}</code>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Economy Fallback</span>
              <code className="font-mono">{agent.fallback_models?.[0] || "None"}</code>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid var(--border-subtle)", paddingTop: 6 }}>
              <span style={{ color: "var(--text-muted)" }}>Substitution Under Pressure</span>
              <span>
                {agent.substitution_enabled ? (
                  <span className="badge ok">Enabled</span>
                ) : (
                  <span className="badge muted">Disabled</span>
                )}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Sessions */}
      <div style={{ marginBottom: 32 }}>
        <div className="section-header">
          <span className="section-title">Governed Sessions ({sessions.length})</span>
        </div>

        {sessions.length === 0 ? (
          <div className="table-container">
            <div className="empty-state">No sessions recorded for this agent yet.</div>
          </div>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Session ID</th>
                  <th>Status</th>
                  <th>Limit</th>
                  <th>Committed</th>
                  <th>Available</th>
                  <th>Closure Reason</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id}>
                    <td className="money" style={{ color: "var(--info)" }}>
                      {s.session_id}
                    </td>
                    <td>
                      <span
                        className={`badge ${
                          s.status === "OPEN"
                            ? "ok"
                            : s.status.includes("BUDGET")
                            ? "danger"
                            : "muted"
                        }`}
                      >
                        {s.status}
                      </span>
                    </td>
                    <td className="money">{usd(s.limit_usd)}</td>
                    <td className="money">{usd(s.committed_usd, 4)}</td>
                    <td className="money">{usd(s.available_usd, 4)}</td>
                    <td style={{ color: "var(--text-muted)", fontSize: 12 }}>
                      {s.close_reason || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modals */}
      {modal === "budget" && (
        <EditAgentBudgetModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            loadAgentData();
          }}
        />
      )}
      {modal === "policy" && (
        <EditModelPolicyModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            loadAgentData();
          }}
        />
      )}
      {modal === "pause" && (
        <PauseAgentModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            loadAgentData();
          }}
        />
      )}
      {modal === "resume" && (
        <ResumeAgentModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            loadAgentData();
          }}
        />
      )}
    </main>
  );
}
