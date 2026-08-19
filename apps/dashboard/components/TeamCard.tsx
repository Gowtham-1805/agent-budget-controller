"use client";

import Link from "next/link";
import { useState } from "react";
import { usd } from "../lib/api";
import type { TeamSummary } from "../lib/types";
import { EditTeamBudgetModal } from "./EditTeamBudgetModal";
import { ArrowRightIcon, UsersIcon } from "./Icons";

interface TeamCardProps {
  team: TeamSummary;
  onUpdated?: () => void;
}

export function TeamCard({ team, onUpdated }: TeamCardProps) {
  const [editing, setEditing] = useState(false);

  const committed = Math.min(100, Math.max(0, team.utilization_percent));
  const isExhausted = team.utilization_percent >= 100;
  const isWarning = team.utilization_percent >= team.warning_threshold_percent && !isExhausted;

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div className="card-header">
        <div>
          <div className="card-subtitle">Organizational Scope</div>
          <h3 style={{ fontSize: 15, marginTop: 2, display: "flex", alignItems: "center", gap: 6 }}>
            <UsersIcon size={15} style={{ color: "var(--text-muted)" }} />
            <Link href={`/teams/${team.team_id}`} style={{ color: "var(--text-primary)" }}>
              {team.team_id}
            </Link>
          </h3>
        </div>

        <div>
          {isExhausted ? (
            <span className="badge danger">100% Blocked</span>
          ) : isWarning ? (
            <span className="badge warn">{team.warning_threshold_percent}% Warning</span>
          ) : (
            <span className="badge ok">Active</span>
          )}
        </div>
      </div>

      {/* Meter */}
      <div className="meter-rail" title={`${committed}% committed`}>
        <div
          className={`meter-fill ${isExhausted ? "danger" : isWarning ? "warn" : ""}`}
          style={{ width: `${committed}%` }}
        />
      </div>

      {/* Financial Metrics */}
      <div style={{ flex: 1, marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: "var(--text-muted)" }}>Periodic Budget</span>
          <span className="money" style={{ fontWeight: 600 }}>
            {usd(team.limit_usd)} ({team.window_type})
          </span>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: "var(--text-muted)" }}>Committed Spend</span>
          <span className="money" style={{ color: "var(--text-primary)" }}>
            {usd(team.committed_usd, 4)} ({team.utilization_percent}%)
          </span>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: "var(--text-muted)" }}>Remaining Allowance</span>
          <span className="money" style={{ color: "var(--ok)" }}>
            {usd(team.available_usd, 4)}
          </span>
        </div>

        <div
          style={{
            borderTop: "1px solid var(--border-subtle)",
            marginTop: 4,
            paddingTop: 8,
            display: "flex",
            justifyContent: "space-between",
            fontSize: 11.5,
          }}
        >
          <span style={{ color: "var(--text-muted)" }}>Governed Members</span>
          <span style={{ fontWeight: 500, color: "var(--text-secondary)" }}>
            {team.agent_count} {team.agent_count === 1 ? "Agent" : "Agents"} &bull; {team.active_sessions} active {team.active_sessions === 1 ? "session" : "sessions"}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px solid var(--border-subtle)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Link href={`/teams/${team.team_id}`} className="btn btn-sm">
          <span>Manage Team</span>
          <ArrowRightIcon size={10} />
        </Link>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setEditing(true)}
        >
          Edit Budget
        </button>
      </div>

      {editing && (
        <EditTeamBudgetModal
          team={team}
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setEditing(false);
            onUpdated?.();
          }}
        />
      )}
    </div>
  );
}
