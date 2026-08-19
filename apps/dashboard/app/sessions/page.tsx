"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRightIcon } from "../../components/Icons";
import { usd } from "../../lib/api";
import type { AgentSummary, SessionView } from "../../lib/types";

export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<string>("ALL");
  const [error, setError] = useState<string | null>(null);

  function loadData() {
    setLoading(true);
    Promise.allSettled([
      fetch("/api/sessions").then((r) => r.json()),
      fetch("/api/agents").then((r) => r.json()),
    ])
      .then(([sessRes, agentRes]) => {
        if (sessRes.status === "fulfilled" && Array.isArray(sessRes.value)) {
          setSessions(sessRes.value);
        }
        if (agentRes.status === "fulfilled" && Array.isArray(agentRes.value)) {
          setAgents(agentRes.value);
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
    loadData();
  }, []);

  async function handleCloseSession(sessionId: string) {
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/close`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to close session");
      }
      loadData();
    } catch (err: any) {
      alert(`Error closing session: ${err.message}`);
    }
  }

  const filteredSessions = sessions.filter((s) => {
    if (selectedAgent !== "ALL" && s.agent_id !== selectedAgent) return false;
    return true;
  });

  const openCount = sessions.filter((s) => s.status === "OPEN").length;
  const closedBudgetCount = sessions.filter((s) => s.status.includes("BUDGET")).length;

  return (
    <main>
      <div className="page-header">
        <div>
          <h1 className="page-title">Session Budgets &amp; Lifecycle</h1>
          <p className="page-description">
            Per-session spend boundaries. Sessions automatically close when limit is exhausted or manually revoked.
          </p>
        </div>

        <Link href="/playground" className="btn btn-primary btn-sm">
          <span>Test in Playground</span>
          <ArrowRightIcon size={11} />
        </Link>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Summary KPI Strip */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Total Sessions</div>
          <div className="stat-value money">{sessions.length}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Active / Open</div>
          <div className="stat-value money" style={{ color: "var(--ok)" }}>
            {openCount}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Closed (Budget Exhausted)</div>
          <div className="stat-value money" style={{ color: closedBudgetCount > 0 ? "var(--warn)" : "var(--text-primary)" }}>
            {closedBudgetCount}
          </div>
        </div>
      </div>

      {/* Filter bar */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md)",
          padding: "8px 12px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 20,
        }}
      >
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Filter by Agent:</span>
        <select
          className="input mono"
          value={selectedAgent}
          onChange={(e) => setSelectedAgent(e.target.value)}
          style={{ width: "auto", padding: "4px 8px", fontSize: 12 }}
        >
          <option value="ALL">All Agents</option>
          {agents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>
              {a.agent_id}
            </option>
          ))}
        </select>
      </div>

      {/* Sessions Table */}
      <div className="table-container">
        {loading ? (
          <div className="empty-state">Loading session records...</div>
        ) : filteredSessions.length === 0 ? (
          <div className="empty-state">No session records found.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Session ID</th>
                <th>Governed Agent</th>
                <th>Status</th>
                <th>Limit</th>
                <th>Committed</th>
                <th>Available</th>
                <th>Closure Reason</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredSessions.map((s) => (
                <tr key={s.session_id}>
                  <td className="money" style={{ color: "var(--info)", fontSize: 12 }}>
                    {s.session_id}
                  </td>
                  <td>
                    <Link href={`/agents/${s.agent_id}`} style={{ fontWeight: 500 }}>
                      {s.agent_id}
                    </Link>
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
                  <td className="money" style={{ color: "var(--ok)" }}>{usd(s.available_usd, 4)}</td>
                  <td style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    {s.close_reason || "—"}
                  </td>
                  <td>
                    {s.status === "OPEN" && (
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => handleCloseSession(s.session_id)}
                      >
                        Close
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
