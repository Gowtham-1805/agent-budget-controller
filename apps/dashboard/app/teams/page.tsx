"use client";

import { useEffect, useState } from "react";
import { CreateTeamModal } from "../../components/CreateTeamModal";
import { PlusIcon } from "../../components/Icons";
import { TeamCard } from "../../components/TeamCard";
import { usd } from "../../lib/api";
import type { TeamSummary } from "../../lib/types";

export default function TeamsPage() {
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadTeams() {
    setLoading(true);
    fetch("/api/teams")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load teams");
        return res.json();
      })
      .then((data) => {
        setTeams(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        setError(err.message);
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

  return (
    <main>
      <div className="page-header">
        <div>
          <h1 className="page-title">Engineering Teams</h1>
          <p className="page-description">
            Top-level organizational spend governance. Every agent operates within a bounded team ceiling.
          </p>
        </div>

        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => setCreating(true)}
        >
          <PlusIcon size={12} />
          <span>Create Team</span>
        </button>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Aggregate Stats Strip */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Total Allocated Budget</div>
          <div className="stat-value money">{usd(totalBudget)}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Total Committed Spend</div>
          <div className="stat-value money">{usd(totalSpend, 4)}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Governed Member Agents</div>
          <div className="stat-value money">{totalAgents}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Active Teams</div>
          <div className="stat-value money">{teams.length}</div>
        </div>
      </div>

      {loading ? (
        <div className="table-container">
          <div className="empty-state">Loading engineering teams...</div>
        </div>
      ) : teams.length === 0 ? (
        <div className="table-container">
          <div className="empty-state">
            No teams provisioned yet. Click &quot;Create Team&quot; above to establish your first team budget.
          </div>
        </div>
      ) : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          {teams.map((t) => (
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
    </main>
  );
}
