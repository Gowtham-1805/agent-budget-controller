"use client";

import { useEffect, useState } from "react";
import { CreateTeamModal } from "../../components/CreateTeamModal";
import {
  PlusIcon,
  RefreshCwIcon,
  UsersIcon,
  AlertCircleIcon,
  ShieldIcon,
  SearchIcon,
  TrendingUpIcon,
} from "../../components/Icons";
import { TeamCard } from "../../components/TeamCard";
import { usd } from "../../lib/api";
import type { TeamSummary } from "../../lib/types";

export default function TeamsPage() {
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadTeams() {
    setLoading(true);
    setError(null);
    fetch("/api/teams")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load teams");
        return res.json();
      })
      .then((data) => {
        setTeams(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        setError(err.message || "Failed to load teams");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadTeams();
  }, []);

  const totalBudget = teams.reduce((acc, t) => acc + Number(t.limit_usd || 0), 0);
  const totalSpend = teams.reduce((acc, t) => acc + Number(t.committed_usd || 0), 0);
  const totalAgents = teams.reduce((acc, t) => acc + (t.agent_count || 0), 0);
  const totalAvailable = Math.max(0, totalBudget - totalSpend);


  // A team with limit_usd === 0 is manually blocked regardless of utilization
  const isManuallyBlocked = (t: TeamSummary) => Number(t.limit_usd) === 0;
  const isExhausted = (t: TeamSummary) => t.utilization_percent >= 100;
  const isWarning = (t: TeamSummary) =>
    !isManuallyBlocked(t) &&
    t.utilization_percent >= (t.warning_threshold_percent ?? 80) &&
    t.utilization_percent < 100;
  const isActive = (t: TeamSummary) =>
    !isManuallyBlocked(t) && !isExhausted(t) && !isWarning(t);

  const warningTeams = teams.filter(isWarning);
  const blockedTeams = teams.filter((t) => isExhausted(t) || isManuallyBlocked(t));
  const activeTeams = teams.filter(isActive);

  const filteredTeams = teams.filter((t) => {
    if (statusFilter === "ACTIVE" && !isActive(t)) return false;
    if (statusFilter === "WARNING" && !isWarning(t)) return false;
    if (statusFilter === "BLOCKED" && !isExhausted(t) && !isManuallyBlocked(t)) return false;

    if (search.trim()) {
      const q = search.toLowerCase();
      return t.team_id.toLowerCase().includes(q);
    }
    return true;
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">Engineering Teams &amp; Organizational Budgets</h1>
          <p className="page-description">
            Top-level organizational spend governance. Every agent operates within an isolated, bounded team budget ceiling.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={loadTeams}
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
            <span>Create Team Budget</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* 4-Metric Aggregate Stats Strip */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Total Allocated Team Budget</div>
          <div className="stat-value money">{usd(totalBudget, 2)}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Total Committed Spend</div>
          <div className="stat-value money">{usd(totalSpend, 4)}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Remaining Spend Allowance</div>
          <div className="stat-value money" style={{ color: "var(--ok)" }}>
            {usd(totalAvailable, 4)}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Governed Member Agents</div>
          <div className="stat-value money" style={{ color: "var(--brand-blue)" }}>
            {totalAgents}
          </div>
        </div>
      </div>

      {/* Filter & Search Bar */}
      <div
        className="shadcn-card"
        style={{
          padding: "14px 18px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        {/* Status Filter Tabs */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            className={`btn btn-sm ${statusFilter === "ALL" ? "btn-primary" : "btn-outline"}`}
            onClick={() => setStatusFilter("ALL")}
          >
            All Teams ({teams.length})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${statusFilter === "ACTIVE" ? "btn-primary" : "btn-outline"}`}
            onClick={() => setStatusFilter("ACTIVE")}
          >
            Active ({activeTeams.length})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${statusFilter === "WARNING" ? "btn-warning" : "btn-outline"}`}
            onClick={() => setStatusFilter("WARNING")}
          >
            Warning ({warningTeams.length})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${statusFilter === "BLOCKED" ? "btn-danger" : "btn-outline"}`}
            onClick={() => setStatusFilter("BLOCKED")}
          >
            Blocked ({blockedTeams.length})
          </button>
        </div>

        {/* Search Input */}
        <div style={{ position: "relative", minWidth: 240 }}>
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}>
            <SearchIcon size={13} />
          </span>
          <input
            type="text"
            className="form-input font-mono"
            placeholder="Search teams by identifier..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 30, fontSize: 12.5 }}
          />
        </div>
      </div>

      {/* Teams Grid */}
      {loading ? (
        <div className="shadcn-card">
          <div className="empty-state">Loading engineering teams...</div>
        </div>
      ) : filteredTeams.length === 0 ? (
        <div className="shadcn-card">
          <div className="empty-state">
            {search || statusFilter !== "ALL"
              ? "No teams found matching the search criteria."
              : "No teams provisioned yet. Click \"Create Team Budget\" above to establish your first team boundary."}
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 18 }}>
          {filteredTeams.map((t) => (
            <TeamCard key={t.team_id} team={t} onUpdated={loadTeams} />
          ))}
        </div>
      )}

      {creating && (
        <CreateTeamModal
          onClose={() => setCreating(false)}
          onSuccess={() => {
            setCreating(false);
            loadTeams();
          }}
        />
      )}
    </div>
  );
}
