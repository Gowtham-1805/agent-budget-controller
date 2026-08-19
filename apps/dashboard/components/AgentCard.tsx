"use client";

import Link from "next/link";
import { useState } from "react";
import { tokens, usd } from "../lib/api";
import type { AgentSummary } from "../lib/types";
import { EditAgentBudgetModal } from "./EditAgentBudgetModal";
import { EditModelPolicyModal } from "./EditModelPolicyModal";
import { ArrowRightIcon, CpuIcon, PauseIcon, PlayIcon, SlidersIcon } from "./Icons";
import { PauseAgentModal } from "./PauseAgentModal";
import { ResumeAgentModal } from "./ResumeAgentModal";

interface AgentCardProps {
  agent: AgentSummary;
  onUpdated?: () => void;
}

export function AgentCard({ agent, onUpdated }: AgentCardProps) {
  const [modal, setModal] = useState<"budget" | "policy" | "pause" | "resume" | null>(null);

  const committed = Math.min(100, Math.max(0, agent.utilization_percent));
  const effective = Math.min(100, Math.max(0, agent.effective_utilization_percent));
  const reservedWidth = Math.max(0, effective - committed);

  const isPaused = agent.status.startsWith("PAUSED");
  const isRunaway = agent.status === "PAUSED_RUNAWAY" || agent.review_required;
  const isExhausted = agent.utilization_percent >= 100;
  const isWarning = agent.utilization_percent >= 80 && !isExhausted;

  return (
    <div
      className="card"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        borderLeft: isRunaway
          ? "3px solid var(--danger)"
          : isPaused
          ? "3px solid var(--warn)"
          : isExhausted
          ? "3px solid var(--danger)"
          : isWarning
          ? "3px solid var(--warn)"
          : "1px solid var(--border)",
      }}
    >
      {/* Card Header */}
      <div className="card-header">
        <div>
          <div className="card-subtitle">Team: {agent.team_id}</div>
          <h3 style={{ fontSize: 14, marginTop: 2, display: "flex", alignItems: "center", gap: 6 }}>
            <CpuIcon size={14} style={{ color: "var(--text-muted)" }} />
            <Link href={`/agents/${agent.agent_id}`} style={{ color: "var(--text-primary)" }}>
              {agent.agent_id}
            </Link>
          </h3>
        </div>

        <div>
          {isRunaway ? (
            <span className="badge danger">Runaway Paused</span>
          ) : isPaused ? (
            <span className="badge warn">Paused</span>
          ) : isExhausted ? (
            <span className="badge danger">100% Blocked</span>
          ) : isWarning ? (
            <span className="badge warn">80% Warning</span>
          ) : (
            <span className="badge ok">Active</span>
          )}
        </div>
      </div>

      {/* Spend Meter */}
      <div className="meter-rail" title={`${committed}% committed, ${effective}% effective exposure`}>
        <div
          className={`meter-fill ${isExhausted ? "danger" : isWarning ? "warn" : ""}`}
          style={{ width: `${committed}%` }}
        />
        <div
          className="meter-flight"
          style={{ left: `${committed}%`, width: `${reservedWidth}%` }}
        />
      </div>

      {/* Core Financial & Token Metrics */}
      <div style={{ flex: 1, marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: "var(--text-muted)" }}>Periodic Budget</span>
          <span className="money" style={{ fontWeight: 600 }}>
            {usd(agent.limit_usd)}
          </span>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: "var(--text-muted)" }}>Committed Spend</span>
          <span className="money" style={{ color: "var(--text-primary)" }}>
            {usd(agent.committed_usd, 4)} ({agent.utilization_percent}%)
          </span>
        </div>

        {Number(agent.reserved_usd) > 0 && (
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
            <span style={{ color: "var(--info)" }}>In Flight (Reserved)</span>
            <span className="money" style={{ color: "var(--info)" }}>
              {usd(agent.reserved_usd, 4)}
            </span>
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
          <span style={{ color: "var(--text-muted)" }}>Available Balance</span>
          <span className="money" style={{ color: "var(--ok)" }}>
            {usd(agent.available_usd, 4)}
          </span>
        </div>

        <div
          style={{
            borderTop: "1px solid var(--border-subtle)",
            marginTop: 4,
            paddingTop: 8,
            display: "flex",
            flexDirection: "column",
            gap: 5,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
            <span style={{ color: "var(--text-muted)" }}>Tokens (In / Out)</span>
            <span className="money">
              {tokens(agent.input_tokens)} / {tokens(agent.output_tokens)}
            </span>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, alignItems: "center" }}>
            <span style={{ color: "var(--text-muted)" }}>Model Policy</span>
            <span className="font-mono" style={{ fontSize: 11, color: "var(--text-secondary)" }}>
              {agent.preferred_model}
              {agent.fallback_models?.length > 0 && (
                <span style={{ color: "var(--text-muted)", marginLeft: 3 }}>
                  &rarr; {agent.fallback_models[0]}
                </span>
              )}
            </span>
          </div>
        </div>

        {isPaused && agent.pause_reason && (
          <div
            style={{
              marginTop: 6,
              padding: "6px 8px",
              background: "var(--surface-raised)",
              borderRadius: "var(--radius-sm)",
              fontSize: 11,
              color: "var(--text-secondary)",
              border: "1px solid var(--border)",
            }}
          >
            <strong style={{ color: "var(--warn)" }}>Pause Reason:</strong> {agent.pause_reason}
          </div>
        )}
      </div>

      {/* Action Footer */}
      <div
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px solid var(--border-subtle)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: 4 }}>
          <Link href={`/agents/${agent.agent_id}`} className="btn btn-sm">
            <span>View</span>
            <ArrowRightIcon size={10} />
          </Link>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setModal("budget")}
            title="Edit Spend Limit"
          >
            Budget
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setModal("policy")}
            title="Model Routing Policy"
          >
            <SlidersIcon size={12} />
          </button>
        </div>

        <div style={{ display: "flex", gap: 4 }}>
          {isPaused ? (
            <button
              type="button"
              className="btn btn-success btn-sm"
              onClick={() => setModal("resume")}
            >
              Resume
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-danger btn-sm"
              onClick={() => setModal("pause")}
            >
              Pause
            </button>
          )}
          <Link
            href={`/playground?agent=${encodeURIComponent(agent.agent_id)}`}
            className="btn btn-primary btn-sm"
          >
            <PlayIcon size={10} />
            <span>Test</span>
          </Link>
        </div>
      </div>

      {/* Interactive Modals */}
      {modal === "budget" && (
        <EditAgentBudgetModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            onUpdated?.();
          }}
        />
      )}
      {modal === "policy" && (
        <EditModelPolicyModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            onUpdated?.();
          }}
        />
      )}
      {modal === "pause" && (
        <PauseAgentModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            onUpdated?.();
          }}
        />
      )}
      {modal === "resume" && (
        <ResumeAgentModal
          agent={agent}
          onClose={() => setModal(null)}
          onSuccess={() => {
            setModal(null);
            onUpdated?.();
          }}
        />
      )}
    </div>
  );
}
