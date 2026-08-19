"use client";

import { useEffect, useState } from "react";
import { AgentCard } from "../../components/AgentCard";
import { CreateAgentModal } from "../../components/CreateAgentModal";
import {
  PlusIcon,
  SearchIcon,
  CpuIcon,
  RefreshCwIcon,
  AlertCircleIcon,
  ShieldIcon,
  TrendingUpIcon,
} from "../../components/Icons";
import { usd } from "../../lib/api";
import type { AgentSummary } from "../../lib/types";

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [filterTeam, setFilterTeam] = useState<string>("ALL");
  const [filterStatus, setFilterStatus] = useState<string>("ALL");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadAgents() {
    setLoading(true);
    setError(null);
    fetch("/api/agents")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to fetch agents");
        return res.json();
      })
      .then((data) => {
        setAgents(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        setError(err.message || "Failed to load agents");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadAgents();
  }, []);

  const teams = Array.from(new Set(agents.map((a) => a.team_id))).filter(Boolean);

  const activeCount = agents.filter((a) => a.status === "ACTIVE").length;
  const pausedCount = agents.filter((a) => a.status.startsWith("PAUSED")).length;
  const warningCount = agents.filter((a) => a.utilization_percent >= 80 && a.utilization_percent < 100).length;
  const totalCommitted = agents.reduce((acc, a) => acc + Number(a.committed_usd || 0), 0);

  const filteredAgents = agents.filter((a) => {
    if (filterTeam !== "ALL" && a.team_id !== filterTeam) return false;
    if (filterStatus === "ACTIVE" && a.status !== "ACTIVE") return false;
    if (filterStatus === "PAUSED" && !a.status.startsWith("PAUSED")) return false;
    if (filterStatus === "WARNING" && (a.utilization_percent < 80 || a.utilization_percent >= 100))
      return false;
    if (filterStatus === "BLOCKED" && a.utilization_percent < 100) return false;
    if (
      search &&
      !a.agent_id.toLowerCase().includes(search.toLowerCase()) &&
      !a.team_id.toLowerCase().includes(search.toLowerCase()) &&
      !a.preferred_model.toLowerCase().includes(search.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">Governed Autonomous Agents</h1>
          <p className="page-description">
            Continuous spending limits, preflight metering, model routing, and velocity circuit breakers per agent.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={loadAgents}
          >
            <RefreshCwIcon size={12} />
            <span>{loading ? "Refreshing..." : "Refresh"}</span>
          </button>

          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setCreating(true)}
          >
            <PlusIcon size={13} />
            <span>Provision Agent</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* 4-Metric Agents Summary Strip */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Total Governed Agents</div>
          <div className="stat-value money">{agents.length}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: "var(--ok)" }}>Active Running Agents</div>
          <div className="stat-value money" style={{ color: "var(--ok)" }}>
            {activeCount}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: "var(--warning)" }}>80% Warning Thresholds</div>
          <div className="stat-value money" style={{ color: warningCount > 0 ? "var(--warning)" : "var(--text-primary)" }}>
            {warningCount}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: pausedCount > 0 ? "var(--danger)" : "var(--text-muted)" }}>
            Circuit Paused / Runaway
          </div>
          <div className="stat-value money" style={{ color: pausedCount > 0 ? "var(--danger)" : "var(--text-primary)" }}>
            {pausedCount}
          </div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div
        className="shadcn-card"
        style={{
          padding: "14px 18px",
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ position: "relative", minWidth: 260, flex: 1 }}>
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}>
            <SearchIcon size={13} />
          </span>
          <input
            type="text"
            className="form-input font-mono"
            placeholder="Search agents by ID, team, or model..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 30, fontSize: 12.5 }}
          />
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 500 }}>Team:</span>
            <select
              className="form-select font-mono"
              value={filterTeam}
              onChange={(e) => setFilterTeam(e.target.value)}
              style={{ width: "auto", padding: "6px 10px", fontSize: 12 }}
            >
              <option value="ALL">All Teams ({teams.length})</option>
              {teams.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 500 }}>Status:</span>
            <select
              className="form-select font-mono"
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              style={{ width: "auto", padding: "6px 10px", fontSize: 12 }}
            >
              <option value="ALL">All Statuses</option>
              <option value="ACTIVE">Active ({activeCount})</option>
              <option value="PAUSED">Paused ({pausedCount})</option>
              <option value="WARNING">80% Warning ({warningCount})</option>
              <option value="BLOCKED">100% Blocked</option>
            </select>
          </div>
        </div>
      </div>

      {/* Agents Grid */}
      {loading ? (
        <div className="shadcn-card">
          <div className="empty-state">Loading governed agents...</div>
        </div>
      ) : filteredAgents.length === 0 ? (
        <div className="shadcn-card">
          <div className="empty-state">
            No agents found matching the current search criteria. Click &quot;Provision Agent&quot; above to create a new agent.
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 18 }}>
          {filteredAgents.map((agent) => (
            <AgentCard
              key={agent.agent_id}
              agent={agent}
              onUpdated={loadAgents}
            />
          ))}
        </div>
      )}

      {/* Create Agent Modal */}
      {creating && (
        <CreateAgentModal
          onClose={() => setCreating(false)}
          onSuccess={() => {
            setCreating(false);
            loadAgents();
          }}
        />
      )}
    </div>
  );
}
