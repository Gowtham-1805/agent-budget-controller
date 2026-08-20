"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { AgentCard } from "@/components/AgentCard";
import { CreateAgentModal } from "@/components/CreateAgentModal";
import { EditTeamBudgetModal } from "@/components/EditTeamBudgetModal";
import { ChevronRightIcon, PlusIcon, SlidersIcon, UsersIcon, AlertCircleIcon, RefreshCwIcon } from "@/components/Icons";
import { usd } from "@/lib/format";
import type { AgentSummary, TeamSummary } from "@/lib/types";

export default function TeamDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const teamId = resolvedParams.id;

  const [team, setTeam] = useState<TeamSummary | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingBudget, setEditingBudget] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadData() {
    setLoading(true);
    setError(null);
    Promise.allSettled([
      fetch("/api/teams").then((r) => r.json()),
      fetch("/api/agents").then((r) => r.json()),
    ])
      .then(([teamsRes, agentsRes]) => {
        if (teamsRes.status === "fulfilled" && Array.isArray(teamsRes.value)) {
          const found = teamsRes.value.find((t: TeamSummary) => t.team_id === teamId);
          if (found) setTeam(found);
          else setError(`Team '${teamId}' not found.`);
        }
        if (agentsRes.status === "fulfilled" && Array.isArray(agentsRes.value)) {
          setAgents(agentsRes.value.filter((a: AgentSummary) => a.team_id === teamId));
        }
      })
      .catch((err) => {
        setError(err.message || "Failed to load team data");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadData();
  }, [teamId]);

  if (loading) {
    return (
      <div className="shadcn-card" style={{ textAlign: "center", padding: 48 }}>
        Loading team details...
      </div>
    );
  }

  if (!team) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="notice-box danger">Team &apos;{teamId}&apos; could not be found.</div>
        <Link href="/teams" className="btn btn-outline btn-sm" style={{ width: "fit-content" }}>
          &larr; Back to Teams
        </Link>
      </div>
    );
  }

  const committed = Math.min(100, Math.max(0, team.utilization_percent));
  const isExhausted = team.utilization_percent >= 100;
  const isWarning = team.utilization_percent >= team.warning_threshold_percent && !isExhausted;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Breadcrumb */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)" }}>
        <Link href="/teams" style={{ color: "var(--text-secondary)", textDecoration: "none" }}>
          Teams
        </Link>
        <ChevronRightIcon size={12} />
        <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{team.team_id}</span>
      </div>

      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">{team.team_id}</h1>
          <p className="page-description">
            Window: <strong>{team.window_type}</strong> &bull; Warning Threshold: <strong>{team.warning_threshold_percent}%</strong> &bull; Governed Members: <strong>{agents.length}</strong>
          </p>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={loadData}
          >
            <RefreshCwIcon size={12} />
            <span>Refresh</span>
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setEditingBudget(true)}
          >
            <SlidersIcon size={12} />
            <span>Edit Team Budget</span>
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setCreatingAgent(true)}
          >
            <PlusIcon size={12} />
            <span>Add Agent</span>
          </button>
        </div>
      </div>

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
            <div className="card-title">Team Financial Utilization</div>
            <div className="card-subtitle">Enforced across all member agents in this scope</div>
          </div>
          <span className={`badge ${isExhausted ? "badge-danger" : isWarning ? "badge-warning" : "badge-ok"}`} style={{ fontSize: 11.5 }}>
            {team.utilization_percent.toFixed(1)}% Utilized
          </span>
        </div>

        <div className="meter-rail">
          <div
            className={`meter-fill ${isExhausted ? "danger" : isWarning ? "warn" : "ok"}`}
            style={{ width: `${committed}%` }}
          />
        </div>

        <div className="stats-strip" style={{ margin: "4px 0 0", border: "1px solid var(--border-app)" }}>
          <div className="stat-cell">
            <div className="stat-label">Budget Ceiling</div>
            <div className="stat-value money">{usd(team.limit_usd, 2)}</div>
          </div>

          <div className="stat-cell">
            <div className="stat-label">Committed Spend</div>
            <div className="stat-value money">{usd(team.committed_usd, 4)}</div>
          </div>

          <div className="stat-cell">
            <div className="stat-label">Remaining Balance</div>
            <div className="stat-value money" style={{ color: "var(--ok)" }}>
              {usd(team.available_usd, 4)}
            </div>
          </div>

          <div className="stat-cell">
            <div className="stat-label">Active Sessions</div>
            <div className="stat-value money" style={{ color: "var(--brand-blue)" }}>
              {team.active_sessions || 0}
            </div>
          </div>
        </div>
      </div>

      {/* Member Agents Grid */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
              Member Governed Agents ({agents.length})
            </h2>
            <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
              Agents drawing from this team&apos;s allocated periodic ceiling
            </p>
          </div>
        </div>

        {agents.length === 0 ? (
          <div className="shadcn-card">
            <div className="empty-state">
              No agents assigned to this team yet. Click &quot;Add Agent&quot; above to provision one.
            </div>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 18 }}>
            {agents.map((agent) => (
              <AgentCard
                key={agent.agent_id}
                agent={agent}
                onUpdated={loadData}
              />
            ))}
          </div>
        )}
      </div>

      {editingBudget && (
        <EditTeamBudgetModal
          team={team}
          onClose={() => setEditingBudget(false)}
          onSuccess={() => {
            setEditingBudget(false);
            loadData();
          }}
        />
      )}

      {creatingAgent && (
        <CreateAgentModal
          initialTeamId={team.team_id}
          onClose={() => setCreatingAgent(false)}
          onSuccess={() => {
            setCreatingAgent(false);
            loadData();
          }}
        />
      )}
    </div>
  );
}
