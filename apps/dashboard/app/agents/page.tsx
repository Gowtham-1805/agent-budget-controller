"use client";

import { useEffect, useState } from "react";
import { AgentCard } from "../../components/AgentCard";
import { CreateAgentModal } from "../../components/CreateAgentModal";
import { PlusIcon, SearchIcon } from "../../components/Icons";
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
    fetch("/api/agents")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to fetch agents");
        return res.json();
      })
      .then((data) => {
        setAgents(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        setError(err.message);
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadAgents();
  }, []);

  const teams = Array.from(new Set(agents.map((a) => a.team_id))).filter(Boolean);

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
      !a.team_id.toLowerCase().includes(search.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  return (
    <main>
      <div className="page-header">
        <div>
          <h1 className="page-title">Governed Autonomous Agents</h1>
          <p className="page-description">
            Continuous spending limits, preflight metering, and velocity circuit breakers per agent.
          </p>
        </div>

        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => setCreating(true)}
        >
          <PlusIcon size={12} />
          <span>Provision Agent</span>
        </button>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Filter and Search Bar */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md)",
          padding: "8px 12px",
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 20,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 220 }}>
          <SearchIcon size={14} style={{ color: "var(--text-muted)" }} />
          <input
            type="text"
            className="input"
            placeholder="Search by agent ID or team..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ border: "none", background: "transparent", padding: 0 }}
          />
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>Team:</span>
            <select
              className="input mono"
              value={filterTeam}
              onChange={(e) => setFilterTeam(e.target.value)}
              style={{ width: "auto", padding: "4px 8px", fontSize: 12 }}
            >
              <option value="ALL">All Teams</option>
              {teams.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>Status:</span>
            <select
              className="input mono"
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              style={{ width: "auto", padding: "4px 8px", fontSize: 12 }}
            >
              <option value="ALL">All Statuses</option>
              <option value="ACTIVE">Active</option>
              <option value="PAUSED">Paused (Admin/Runaway)</option>
              <option value="WARNING">Warning (80%+)</option>
              <option value="BLOCKED">Blocked (100%)</option>
            </select>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="table-container">
          <div className="empty-state">Loading governed agents...</div>
        </div>
      ) : filteredAgents.length === 0 ? (
        <div className="table-container">
          <div className="empty-state">
            {agents.length === 0
              ? "No agents provisioned yet. Click 'Provision Agent' to establish governance."
              : "No agents matched the selected filters."}
          </div>
        </div>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(330px, 1fr))" }}>
          {filteredAgents.map((agent) => (
            <AgentCard key={agent.agent_id} agent={agent} onUpdated={loadAgents} />
          ))}
        </div>
      )}

      {creating && (
        <CreateAgentModal
          onClose={() => setCreating(false)}
          onSuccess={() => {
            setCreating(false);
            loadAgents();
          }}
        />
      )}
    </main>
  );
}
