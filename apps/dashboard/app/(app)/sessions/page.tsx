"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowRightIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldIcon,
  TerminalIcon,
  CpuIcon,
  CheckCircleIcon,
  AlertCircleIcon,
  LockIcon,
} from "@/components/Icons";
import { usd } from "@/lib/format";
import type { AgentSummary, SessionView } from "@/lib/types";

export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<string>("ALL");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  function loadData() {
    setLoading(true);
    setError(null);
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
        setError(err.message || "Failed to load session records");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadData();
  }, []);

  async function handleCloseSession(sessionId: string) {
    if (!confirm(`Are you sure you want to immediately close session '${sessionId}'?`)) return;
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

  const openCount = sessions.filter((s) => s.status === "OPEN").length;
  const closedBudgetCount = sessions.filter((s) => s.status.includes("BUDGET")).length;
  const closedUserCount = sessions.filter((s) => s.status.includes("USER") || s.status.includes("ADMIN")).length;
  const totalSessionSpend = sessions.reduce((acc, s) => acc + Number(s.committed_usd || 0), 0);

  const filteredSessions = sessions.filter((s) => {
    if (selectedAgent !== "ALL" && s.agent_id !== selectedAgent) return false;
    if (statusFilter === "OPEN" && s.status !== "OPEN") return false;
    if (statusFilter === "BUDGET" && !s.status.includes("BUDGET")) return false;
    if (statusFilter === "CLOSED" && s.status === "OPEN") return false;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      return (
        s.session_id.toLowerCase().includes(q) ||
        s.agent_id.toLowerCase().includes(q) ||
        (s.close_reason && s.close_reason.toLowerCase().includes(q))
      );
    }
    return true;
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">Session Budgets &amp; Lifecycle</h1>
          <p className="page-description">
            Sub-agent and conversational spend boundaries. Sessions automatically close when limit is reached with zero overspend.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={loadData}
          >
            <RefreshCwIcon size={12} />
            <span>{loading ? "Refreshing..." : "Refresh"}</span>
          </button>

          <Link href="/playground" className="btn btn-primary btn-sm">
            <TerminalIcon size={12} />
            <span>Simulate in Playground</span>
          </Link>
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* 4-Metric Session Summary Strip */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Total Tracked Sessions</div>
          <div className="stat-value money">{sessions.length}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: "var(--ok)" }}>Active / Open Sessions</div>
          <div className="stat-value money" style={{ color: "var(--ok)" }}>
            {openCount}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: "var(--warning)" }}>Closed (Budget Exhausted)</div>
          <div className="stat-value money" style={{ color: closedBudgetCount > 0 ? "var(--warning)" : "var(--text-primary)" }}>
            {closedBudgetCount}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Total Session Spend</div>
          <div className="stat-value money" style={{ color: "var(--brand-blue)" }}>
            {usd(totalSessionSpend, 4)}
          </div>
        </div>
      </div>

      {/* Filter & Search Bar */}
      <div className="shadcn-card" style={{ padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          {/* Status Filter Tabs */}
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className={`btn btn-sm ${statusFilter === "ALL" ? "btn-primary" : "btn-outline"}`}
              onClick={() => setStatusFilter("ALL")}
            >
              All ({sessions.length})
            </button>
            <button
              type="button"
              className={`btn btn-sm ${statusFilter === "OPEN" ? "btn-primary" : "btn-outline"}`}
              onClick={() => setStatusFilter("OPEN")}
            >
              Open ({openCount})
            </button>
            <button
              type="button"
              className={`btn btn-sm ${statusFilter === "BUDGET" ? "btn-danger" : "btn-outline"}`}
              onClick={() => setStatusFilter("BUDGET")}
            >
              Budget Closed ({closedBudgetCount})
            </button>
          </div>

          {/* Agent Filter Dropdown */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 500 }}>Agent:</span>
            <select
              className="form-select font-mono"
              value={selectedAgent}
              onChange={(e) => setSelectedAgent(e.target.value)}
              style={{ width: "auto", minWidth: 160, fontSize: 12 }}
            >
              <option value="ALL">All Agents</option>
              {agents.map((a) => (
                <option key={a.agent_id} value={a.agent_id}>
                  {a.agent_id}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Search Input */}
        <div style={{ position: "relative", minWidth: 220 }}>
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}>
            <SearchIcon size={13} />
          </span>
          <input
            type="text"
            className="form-input font-mono"
            placeholder="Search session ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{ paddingLeft: 30, fontSize: 12.5 }}
          />
        </div>
      </div>

      {/* Sessions Table */}
      <div className="shadcn-card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="table-container" style={{ border: "none" }}>
          {loading ? (
            <div className="empty-state">Loading session records...</div>
          ) : filteredSessions.length === 0 ? (
            <div className="empty-state">
              No session records found. Use the Playground with a session ID to establish a session budget.
            </div>
          ) : (
            <table className="shadcn-table">
              <thead>
                <tr>
                  <th>Session Identifier</th>
                  <th>Governed Agent</th>
                  <th>Status</th>
                  <th>Budget Limit</th>
                  <th>Committed Spend</th>
                  <th>Available Balance</th>
                  <th>Utilization</th>
                  <th>Closure Reason</th>
                  <th style={{ textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredSessions.map((s) => {
                  const limitNum = Number(s.limit_usd || 0);
                  const commNum = Number(s.committed_usd || 0);
                  const utilPercent = limitNum > 0 ? (commNum / limitNum) * 100 : 0;
                  const isOpen = s.status === "OPEN";

                  return (
                    <tr key={s.session_id}>
                      <td>
                        <code style={{ fontSize: 12, fontWeight: 600, color: "var(--brand-blue)" }}>
                          {s.session_id}
                        </code>
                      </td>

                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <CpuIcon size={12} className="text-secondary" />
                          <Link
                            href={`/agents/${s.agent_id}`}
                            style={{ fontWeight: 600, color: "var(--text-primary)", textDecoration: "none", fontSize: 12.5 }}
                          >
                            {s.agent_id}
                          </Link>
                        </div>
                      </td>

                      <td>
                        <span
                          className={`badge ${
                            isOpen
                              ? "badge-ok"
                              : s.status.includes("BUDGET")
                              ? "badge-danger"
                              : "badge-neutral"
                          }`}
                          style={{ fontSize: 10.5 }}
                        >
                          {s.status}
                        </span>
                      </td>

                      <td className="money" style={{ fontSize: 12.5 }}>
                        {usd(s.limit_usd, 2)}
                      </td>

                      <td className="money" style={{ fontWeight: 600, fontSize: 12.5 }}>
                        {usd(s.committed_usd, 4)}
                      </td>

                      <td className="money" style={{ color: "var(--ok)", fontWeight: 600, fontSize: 12.5 }}>
                        {usd(s.available_usd, 4)}
                      </td>

                      <td style={{ minWidth: 110 }}>
                        <div className="meter-rail">
                          <div
                            className={`meter-fill ${utilPercent >= 100 ? "danger" : utilPercent >= 80 ? "warn" : "ok"}`}
                            style={{ width: `${Math.min(100, Math.max(2, utilPercent))}%` }}
                          />
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, textAlign: "right" }}>
                          {utilPercent.toFixed(0)}%
                        </div>
                      </td>

                      <td>
                        <span style={{ fontSize: 11.5, color: s.close_reason ? "var(--danger)" : "var(--text-muted)" }}>
                          {s.close_reason || "Active Session"}
                        </span>
                      </td>

                      <td style={{ textAlign: "right" }}>
                        {isOpen ? (
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            style={{ color: "var(--danger)", borderColor: "var(--danger-border)", fontSize: 11 }}
                            onClick={() => handleCloseSession(s.session_id)}
                          >
                            Close
                          </button>
                        ) : (
                          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Closed</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
