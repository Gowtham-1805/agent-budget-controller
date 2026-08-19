"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { AgentCard } from "../../../components/AgentCard";
import { CreateAgentModal } from "../../../components/CreateAgentModal";
import { EditTeamBudgetModal } from "../../../components/EditTeamBudgetModal";
import { ChevronRightIcon, PlusIcon, SlidersIcon } from "../../../components/Icons";
import { usd } from "../../../lib/api";
import type { TeamSummary } from "../../../lib/types";

export default function TeamDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const teamId = resolvedParams.id;

  const [team, setTeam] = useState<TeamSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingBudget, setEditingBudget] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadTeam() {
    setLoading(true);
    fetch(`/api/teams`)
      .then((res) => res.json())
      .then((teams: TeamSummary[]) => {
        const found = teams.find((t) => t.team_id === teamId);
        if (found) {
          setTeam(found);
        } else {
          setError(`Team '${teamId}' not found.`);
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
    loadTeam();
  }, [teamId]);

  if (loading) {
    return (
      <main>
        <div className="table-container">
          <div className="empty-state">Loading team details...</div>
        </div>
      </main>
    );
  }

  if (!team) {
    return (
      <main>
        <div className="notice-box danger">Team &apos;{teamId}&apos; could not be found.</div>
        <Link href="/teams" className="btn btn-sm" style={{ marginTop: 12 }}>
          &larr; Back to Teams
        </Link>
      </main>
    );
  }

  const committed = Math.min(100, Math.max(0, team.utilization_percent));
  const isExhausted = team.utilization_percent >= 100;
  const isWarning = team.utilization_percent >= team.warning_threshold_percent && !isExhausted;

  return (
    <main>
      {/* Breadcrumb */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)", marginBottom: 16 }}>
        <Link href="/teams" style={{ color: "var(--text-secondary)" }}>
          Teams
        </Link>
        <ChevronRightIcon size={12} />
        <span style={{ color: "var(--text-primary)" }}>{team.team_id}</span>
      </div>

      <div className="page-header">
        <div>
          <h1 className="page-title">{team.team_id}</h1>
          <p className="page-description">
            Window: <strong>{team.window_type}</strong> &bull; Warning Threshold:{" "}
            <strong>{team.warning_threshold_percent}%</strong>
          </p>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="btn btn-sm"
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
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Spend Meter & Stats Card */}
      <div className="card" style={{ marginBottom: 28 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>Team Financial Utilization</span>
          <span className={`badge ${isExhausted ? "danger" : isWarning ? "warn" : "ok"}`}>
            {team.utilization_percent}% Utilized
          </span>
        </div>

        <div className="meter-rail" style={{ height: 6, marginBottom: 16 }}>
          <div
            className={`meter-fill ${isExhausted ? "danger" : isWarning ? "warn" : ""}`}
            style={{ width: `${committed}%` }}
          />
        </div>

        <div className="stats-strip" style={{ border: "none", background: "transparent", margin: 0 }}>
          <div className="stat-cell" style={{ padding: "0 16px 0 0" }}>
            <div className="stat-label">Budget Ceiling</div>
            <div className="stat-value money">{usd(team.limit_usd)}</div>
          </div>

          <div className="stat-cell" style={{ padding: "0 16px" }}>
            <div className="stat-label">Committed Spend</div>
            <div className="stat-value money">{usd(team.committed_usd, 4)}</div>
          </div>

          <div className="stat-cell" style={{ padding: "0 16px" }}>
            <div className="stat-label">Remaining Balance</div>
            <div className="stat-value money" style={{ color: "var(--ok)" }}>
              {usd(team.available_usd, 4)}
            </div>
          </div>

          <div className="stat-cell" style={{ padding: "0 0 0 16px", borderRight: "none" }}>
            <div className="stat-label">Active Sessions</div>
            <div className="stat-value money">{team.active_sessions}</div>
          </div>
        </div>
      </div>

      {/* Member Agents Grid */}
      <div style={{ marginBottom: 32 }}>
        <div className="section-header">
          <span className="section-title">Team Member Agents ({team.agents?.length || 0})</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setCreatingAgent(true)}
          >
            <PlusIcon size={12} />
            <span>Add Member Agent</span>
          </button>
        </div>

        {(!team.agents || team.agents.length === 0) ? (
          <div className="table-container">
            <div className="empty-state">
              No agents currently assigned to this team. Click &quot;Add Member Agent&quot; to provision a new governed agent.
            </div>
          </div>
        ) : (
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
            {team.agents.map((agent) => (
              <AgentCard key={agent.agent_id} agent={agent} onUpdated={loadTeam} />
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
            loadTeam();
          }}
        />
      )}

      {creatingAgent && (
        <CreateAgentModal
          initialTeamId={team.team_id}
          onClose={() => setCreatingAgent(false)}
          onSuccess={() => {
            setCreatingAgent(false);
            loadTeam();
          }}
        />
      )}
    </main>
  );
}
