"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { EditAgentBudgetModal } from "../../../components/EditAgentBudgetModal";
import { EditModelPolicyModal } from "../../../components/EditModelPolicyModal";
import {
  ChevronRightIcon,
  PlayIcon,
  SlidersIcon,
  CpuIcon,
  ShieldIcon,
  RefreshCwIcon,
  AlertCircleIcon,
  TerminalIcon,
  BookOpenIcon,
} from "../../../components/Icons";
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
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [modal, setModal] = useState<"budget" | "policy" | "pause" | "resume" | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function loadAgentData() {
    setLoading(true);
    setError(null);
    Promise.allSettled([
      fetch(`/api/agents`).then((r) => r.json()),
      fetch(`/api/sessions?agent_id=${encodeURIComponent(agentId)}`).then((r) => r.json()),
      fetch(`/api/ledger?agent_id=${encodeURIComponent(agentId)}&limit=10`).then((r) => r.ok ? r.json() : []).then((d) => Array.isArray(d) ? d : d.entries || []),
    ])
      .then(([agentsRes, sessRes, ledRes]) => {
        if (agentsRes.status === "fulfilled" && Array.isArray(agentsRes.value)) {
          const found = agentsRes.value.find((a: AgentSummary) => a.agent_id === agentId);
          if (found) setAgent(found);
          else setError(`Agent '${agentId}' not found.`);
        }
        if (sessRes.status === "fulfilled" && Array.isArray(sessRes.value)) {
          setSessions(sessRes.value.filter((s: SessionView) => s.agent_id === agentId));
        }
        if (ledRes.status === "fulfilled" && Array.isArray(ledRes.value)) {
          setLedger(ledRes.value);
        }
      })
      .catch((err) => {
        setError(err.message || "Failed to load agent details");
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
      <div className="shadcn-card" style={{ textAlign: "center", padding: 48 }}>
        Loading agent details...
      </div>
    );
  }

  if (!agent) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="notice-box danger">Agent &apos;{agentId}&apos; could not be found.</div>
        <Link href="/agents" className="btn btn-outline btn-sm" style={{ width: "fit-content" }}>
          &larr; Back to Agents
        </Link>
      </div>
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
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Breadcrumb */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)" }}>
        <Link href="/agents" style={{ color: "var(--text-secondary)", textDecoration: "none" }}>
          Agents
        </Link>
        <ChevronRightIcon size={12} />
        <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{agent.agent_id}</span>
      </div>

      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h1 className="page-title">{agent.agent_id}</h1>
            {isRunaway ? (
              <span className="badge badge-danger">Runaway Paused</span>
            ) : isPaused ? (
              <span className="badge badge-warning">Paused</span>
            ) : isExhausted ? (
              <span className="badge badge-danger">100% Blocked</span>
            ) : isWarning ? (
              <span className="badge badge-warning">80% Warning</span>
            ) : (
              <span className="badge badge-ok">Active</span>
            )}
          </div>
          <p className="page-description">
            Team: <Link href={`/teams/${agent.team_id}`} style={{ color: "var(--brand-blue)", textDecoration: "none", fontWeight: 600 }}>{agent.team_id}</Link> &bull; Window: <strong>{agent.window_type}</strong> &bull; Output Ceiling: <strong>{agent.default_max_output_tokens} tokens/call</strong>
          </p>
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={loadAgentData}
          >
            <RefreshCwIcon size={12} />
            <span>Refresh</span>
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setModal("budget")}
          >
            <SlidersIcon size={12} />
            <span>Edit Budget</span>
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setModal("policy")}
          >
            <span>Model Routing</span>
          </button>
          {isPaused ? (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              style={{ backgroundColor: "var(--ok)", borderColor: "var(--ok)" }}
              onClick={() => setModal("resume")}
            >
              Resume Agent
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-outline btn-sm"
              style={{ color: "var(--danger)", borderColor: "var(--danger-border)" }}
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
            <span>Test in Playground</span>
          </Link>
        </div>
      </div>

      {isPaused && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <div>
            <strong>Agent is administratively paused:</strong> {agent.pause_reason || "Intervention active."}
            <div style={{ marginTop: 2, fontSize: 12 }}>
              New inference calls are locked at the gateway. Click &quot;Resume Agent&quot; with a justification to restore service.
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* Spend Meter & Stats Card */}
      <div className="shadcn-card" style={{ display: "flex", flexDirection: "column", gap: 14, padding: 22 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div className="card-title">Budget Consumption &amp; In-Flight Hold</div>
            <div className="card-subtitle">Real-time financial exposure evaluated atomically before provider dispatch</div>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <span className={`badge ${isExhausted ? "badge-danger" : isWarning ? "badge-warning" : "badge-ok"}`} style={{ fontSize: 11.5 }}>
              {agent.utilization_percent.toFixed(1)}% Committed
            </span>
            {effective > committed && (
              <span className="badge badge-indigo" style={{ fontSize: 11.5 }}>
                {effective.toFixed(1)}% Effective Exposure
              </span>
            )}
          </div>
        </div>

        <div className="meter-rail">
          <div
            className={`meter-fill ${isExhausted ? "danger" : isWarning ? "warn" : "ok"}`}
            style={{ width: `${committed}%` }}
          />
          <div
            className="meter-flight"
            style={{ left: `${committed}%`, width: `${reservedWidth}%` }}
          />
        </div>

        <div className="stats-strip" style={{ margin: "4px 0 0", border: "1px solid var(--border-app)" }}>
          <div className="stat-cell">
            <div className="stat-label">Periodic Cap</div>
            <div className="stat-value money">{usd(agent.limit_usd, 2)}</div>
          </div>

          <div className="stat-cell">
            <div className="stat-label">Committed Spend</div>
            <div className="stat-value money">{usd(agent.committed_usd, 4)}</div>
          </div>

          <div className="stat-cell">
            <div className="stat-label" style={{ color: Number(agent.reserved_usd) > 0 ? "var(--cyan)" : "var(--text-muted)" }}>
              In-Flight (Reserved)
            </div>
            <div className="stat-value money" style={{ color: "var(--cyan)" }}>
              {usd(agent.reserved_usd, 4)}
            </div>
          </div>

          <div className="stat-cell">
            <div className="stat-label">Available Balance</div>
            <div className="stat-value money" style={{ color: "var(--ok)" }}>
              {usd(agent.available_usd, 4)}
            </div>
          </div>
        </div>
      </div>

      {/* Detail Split: Token Accounting & Model Policy */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div className="shadcn-card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="card-header" style={{ marginBottom: 4 }}>
            <div>
              <div className="card-title">Token Accounting</div>
              <div className="card-subtitle">Cumulative input and output token consumption</div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 12.5 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Cumulative Input Tokens</span>
              <span className="money font-mono">{tokens(agent.input_tokens)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--text-muted)" }}>Cumulative Output Tokens</span>
              <span className="money font-mono">{tokens(agent.output_tokens)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid var(--border-app)", paddingTop: 8 }}>
              <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>Total Token Volume</span>
              <span className="money font-mono" style={{ fontWeight: 700 }}>
                {tokens(agent.input_tokens + agent.output_tokens)}
              </span>
            </div>
          </div>
        </div>

        <div className="shadcn-card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="card-header" style={{ marginBottom: 4 }}>
            <div>
              <div className="card-title">Model Policy &amp; Degradation Fallbacks</div>
              <div className="card-subtitle">Dynamic substitution routing rules</div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 12.5 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ color: "var(--text-muted)" }}>Preferred Model</span>
              <code className="font-mono" style={{ fontSize: 12, backgroundColor: "var(--bg-muted)", padding: "2px 6px", borderRadius: 4 }}>
                {agent.preferred_model}
              </code>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ color: "var(--text-muted)" }}>Economy Fallback</span>
              <code className="font-mono" style={{ fontSize: 12, backgroundColor: "var(--bg-muted)", padding: "2px 6px", borderRadius: 4 }}>
                {agent.fallback_models?.[0] || "None"}
              </code>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--border-app)", paddingTop: 8 }}>
              <span style={{ color: "var(--text-muted)" }}>Substitution Under Pressure</span>
              <span className={`badge ${agent.substitution_enabled ? "badge-ok" : "badge-neutral"}`} style={{ fontSize: 11 }}>
                {agent.substitution_enabled ? "Enabled" : "Disabled"}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Governed Sessions */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Active &amp; Historical Sessions ({sessions.length})
            </h2>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
              Conversational session scopes drawing against this agent
            </p>
          </div>
        </div>

        {sessions.length === 0 ? (
          <div className="shadcn-card">
            <div className="empty-state">No sessions recorded for this agent yet.</div>
          </div>
        ) : (
          <div className="shadcn-card" style={{ padding: 0, overflow: "hidden" }}>
            <div className="table-container" style={{ border: "none" }}>
              <table className="shadcn-table">
                <thead>
                  <tr>
                    <th>Session ID</th>
                    <th>Status</th>
                    <th>Session Limit</th>
                    <th>Committed Spend</th>
                    <th>Available Balance</th>
                    <th>Closure Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.session_id}>
                      <td>
                        <code style={{ fontSize: 12, color: "var(--brand-blue)" }}>{s.session_id}</code>
                      </td>
                      <td>
                        <span className={`badge ${s.status === "OPEN" ? "badge-ok" : s.status.includes("BUDGET") ? "badge-danger" : "badge-neutral"}`} style={{ fontSize: 10.5 }}>
                          {s.status}
                        </span>
                      </td>
                      <td className="money">{usd(s.limit_usd, 2)}</td>
                      <td className="money" style={{ fontWeight: 600 }}>{usd(s.committed_usd, 4)}</td>
                      <td className="money" style={{ color: "var(--ok)", fontWeight: 600 }}>{usd(s.available_usd, 4)}</td>
                      <td style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{s.close_reason || "Active"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Interactive Modals */}
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
    </div>
  );
}
