"use client";

import Link from "next/link";
import { useState } from "react";
import { usd } from "../lib/api";
import type { TeamSummary } from "../lib/types";
import { BlockTeamModal } from "./BlockTeamModal";
import { UnblockTeamModal } from "./UnblockTeamModal";
import { EditTeamBudgetModal } from "./EditTeamBudgetModal";
import { ArrowRightIcon, UsersIcon, SlidersIcon } from "./Icons";

interface TeamCardProps {
  team: TeamSummary;
  onUpdated?: () => void;
}

export function TeamCard({ team, onUpdated }: TeamCardProps) {
  const [editing, setEditing] = useState(false);
  const [blocking, setBlocking] = useState(false);
  const [unblocking, setUnblocking] = useState(false);

  const committed = Math.min(100, Math.max(0, team.utilization_percent));
  const isExhausted = team.utilization_percent >= 100;
  const isWarning = team.utilization_percent >= (team.warning_threshold_percent ?? 80) && !isExhausted;
  // A team is "manually blocked" if its budget limit is $0.00
  const isManuallyBlocked = Number(team.limit_usd) === 0;

  const normalizedWindow =
    team.window_type === "MONTH" ? "MONTHLY"
    : team.window_type === "WEEK" ? "WEEKLY"
    : team.window_type === "DAY" ? "DAILY"
    : team.window_type || "MONTHLY";

  return (
    <div
      className="shadcn-card"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        padding: 20,
        borderLeft: isManuallyBlocked
          ? "4px solid var(--danger)"
          : isExhausted
          ? "4px solid var(--danger)"
          : isWarning
          ? "4px solid var(--warning)"
          : "1px solid var(--border-card)",
      }}
    >
      {/* Header */}
      <div className="card-header" style={{ marginBottom: 10 }}>
        <div>
          <span className="badge badge-neutral" style={{ fontSize: 10.5, marginBottom: 4 }}>
            Organizational Scope
          </span>
          <h3 style={{ fontSize: 15, fontWeight: 700, display: "flex", alignItems: "center", gap: 6, color: "var(--text-primary)" }}>
            <UsersIcon size={15} className="text-secondary" />
            <Link href={`/teams/${team.team_id}`} style={{ color: "var(--text-primary)", textDecoration: "none" }}>
              {team.team_id}
            </Link>
          </h3>
        </div>

        <div>
          {isManuallyBlocked ? (
            <span className="badge badge-danger">Manually Blocked</span>
          ) : isExhausted ? (
            <span className="badge badge-danger">100% Blocked</span>
          ) : isWarning ? (
            <span className="badge badge-warning">{team.warning_threshold_percent ?? 80}% Warning</span>
          ) : (
            <span className="badge badge-ok">Active</span>
          )}
        </div>
      </div>

      {/* Meter */}
      <div style={{ marginBottom: 12 }}>
        <div className="meter-rail" title={`${committed}% committed`}>
          <div
            className={`meter-fill ${isExhausted || isManuallyBlocked ? "danger" : isWarning ? "warn" : "ok"}`}
            style={{ width: isManuallyBlocked ? "100%" : `${committed}%` }}
          />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
          <span>{normalizedWindow} window</span>
          <span className="money font-mono">
            {isManuallyBlocked ? "Budget: $0 — Blocked" : `${team.utilization_percent.toFixed(1)}% utilized`}
          </span>
        </div>
      </div>

      {/* Financial Metrics */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
          <span style={{ color: "var(--text-muted)" }}>Periodic Budget</span>
          <span className="money" style={{ fontWeight: 700, color: isManuallyBlocked ? "var(--danger)" : "var(--text-primary)" }}>
            {usd(team.limit_usd, 2)}
          </span>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
          <span style={{ color: "var(--text-muted)" }}>Committed Spend</span>
          <span className="money" style={{ color: "var(--text-primary)", fontWeight: 600 }}>
            {usd(team.committed_usd, 4)}
          </span>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
          <span style={{ color: "var(--text-muted)" }}>Available Balance</span>
          <span className="money" style={{ color: isManuallyBlocked ? "var(--danger)" : "var(--ok)", fontWeight: 700 }}>
            {usd(team.available_usd, 4)}
          </span>
        </div>

        <div
          style={{
            borderTop: "1px solid var(--border-app)",
            marginTop: 6,
            paddingTop: 8,
            display: "flex",
            justifyContent: "space-between",
            fontSize: 11.5,
          }}
        >
          <span style={{ color: "var(--text-muted)" }}>Governed Members</span>
          <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>
            {team.agent_count} {team.agent_count === 1 ? "Agent" : "Agents"} &bull; {team.active_sessions} {team.active_sessions === 1 ? "session" : "sessions"}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div
        style={{
          marginTop: 14,
          paddingTop: 10,
          borderTop: "1px solid var(--border-app)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: 4 }}>
          <Link href={`/teams/${team.team_id}`} className="btn btn-outline btn-sm">
            <span>Manage</span>
            <ArrowRightIcon size={11} />
          </Link>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setEditing(true)}
            title="Edit budget ceiling"
          >
            <SlidersIcon size={11} />
            <span>Budget</span>
          </button>
        </div>

        {/* Block / Unblock toggle */}
        {isManuallyBlocked || isExhausted ? (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            style={{ backgroundColor: "var(--ok)", borderColor: "var(--ok)" }}
            onClick={() => setUnblocking(true)}
            title="Restore budget and allow member agents to spend"
          >
            Unblock Team
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-outline btn-sm"
            style={{ color: "var(--danger)", borderColor: "var(--danger-border, #fca5a5)" }}
            onClick={() => setBlocking(true)}
            title="Set budget to $0 to immediately stop all member agents"
          >
            Block Team
          </button>
        )}
      </div>

      {/* Modals */}
      {editing && (
        <EditTeamBudgetModal
          team={team}
          onClose={() => setEditing(false)}
          onSuccess={() => { setEditing(false); onUpdated?.(); }}
        />
      )}
      {blocking && (
        <BlockTeamModal
          team={team}
          onClose={() => setBlocking(false)}
          onSuccess={() => { setBlocking(false); onUpdated?.(); }}
        />
      )}
      {unblocking && (
        <UnblockTeamModal
          team={team}
          onClose={() => setUnblocking(false)}
          onSuccess={() => { setUnblocking(false); onUpdated?.(); }}
        />
      )}
    </div>
  );
}
