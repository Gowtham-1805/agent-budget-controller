"use client";

import Link from "next/link";
import { useState } from "react";
import { tokens, usd } from "../lib/format";
import type { AgentSummary } from "../lib/types";
import { EditAgentBudgetModal } from "./EditAgentBudgetModal";
import { EditModelPolicyModal } from "./EditModelPolicyModal";
import { ArrowRightIcon, CpuIcon, PauseIcon, PlayIcon, SlidersIcon, ShieldIcon } from "./Icons";
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
      className="shadcn-card"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        padding: 20,
        borderLeft: isRunaway
          ? "4px solid var(--danger)"
          : isPaused
          ? "4px solid var(--warning)"
          : isExhausted
          ? "4px solid var(--danger)"
          : isWarning
          ? "4px solid var(--warning)"
          : "1px solid var(--border-card)",
      }}
    >
      {/* Card Header */}
      <div className="card-header" style={{ marginBottom: 10 }}>
        <div>
          <span className="badge badge-neutral" style={{ fontSize: 10.5, marginBottom: 4 }}>
            Team: {agent.team_id}
          </span>
          <h3 style={{ fontSize: 14.5, fontWeight: 700, display: "flex", alignItems: "center", gap: 6, color: "var(--text-primary)" }}>
            <CpuIcon size={14} className="text-secondary" />
            <Link href={`/agents/${agent.agent_id}`} style={{ color: "var(--text-primary)", textDecoration: "none" }}>
              {agent.agent_id}
            </Link>
          </h3>
        </div>

        <div>
          {isRunaway ? (
            <span className="badge badge-danger">Runaway Paused</span>
          ) : isPaused ? (
            <span className="badge badge-warning">Paused</span>
          ) : isExhausted ? (
            <span className="badge badge-danger">100% Blocked</span>
          ) : isWarning ? (
            <span className="badge badge-warning">80% Warning</span>
          ) : (
            <span className="badge badge-ok">Active</span>
          )}
        </div>
      </div>

      {/* Spend Meter */}
      <div style={{ marginBottom: 12 }}>
        <div className="meter-rail" title={`${committed}% committed, ${effective}% effective exposure`}>
          <div
            className={`meter-fill ${isExhausted ? "danger" : isWarning ? "warn" : "ok"}`}
            style={{ width: `${committed}%` }}
          />
          <div
            className="meter-flight"
            style={{ left: `${committed}%`, width: `${reservedWidth}%` }}
          />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
          <span>{agent.window_type} ceiling</span>
          <span className="money font-mono">{agent.utilization_percent.toFixed(1)}% utilized</span>
        </div>
      </div>

      {/* Core Financial & Token Metrics */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
          <span style={{ color: "var(--text-muted)" }}>Periodic Budget</span>
          <span className="money" style={{ fontWeight: 700, color: "var(--text-primary)" }}>
            {usd(agent.limit_usd, 2)}
          </span>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
          <span style={{ color: "var(--text-muted)" }}>Committed Spend</span>
          <span className="money" style={{ color: "var(--text-primary)", fontWeight: 600 }}>
            {usd(agent.committed_usd, 4)}
          </span>
        </div>

        {Number(agent.reserved_usd) > 0 && (
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
            <span style={{ color: "var(--cyan)" }}>In Flight (Reserved)</span>
            <span className="money" style={{ color: "var(--cyan)", fontWeight: 600 }}>
              {usd(agent.reserved_usd, 4)}
            </span>
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
          <span style={{ color: "var(--text-muted)" }}>Available Balance</span>
          <span className="money" style={{ color: "var(--ok)", fontWeight: 700 }}>
            {usd(agent.available_usd, 4)}
          </span>
        </div>

        <div
          style={{
            borderTop: "1px solid var(--border-app)",
            marginTop: 4,
            paddingTop: 8,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5 }}>
            <span style={{ color: "var(--text-muted)" }}>Tokens (In / Out)</span>
            <span className="money font-mono">
              {tokens(agent.input_tokens)} / {tokens(agent.output_tokens)}
            </span>
          </div>

          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, alignItems: "center" }}>
            <span style={{ color: "var(--text-muted)" }}>Model Routing</span>
            <span className="font-mono" style={{ fontSize: 11 }}>
              <code style={{ backgroundColor: "var(--bg-muted)", padding: "2px 6px", borderRadius: 4 }}>
                {agent.preferred_model}
              </code>
              {agent.fallback_models?.length > 0 && (
                <span style={{ color: "var(--text-muted)", marginLeft: 3 }}>
                  &rarr; {agent.fallback_models[0]}
                </span>
              )}
            </span>
          </div>
        </div>

        {isPaused && agent.pause_reason && (
          <div className="notice-box warning" style={{ marginTop: 6, padding: "6px 10px", fontSize: 11.5 }}>
            <strong>Pause Reason:</strong> {agent.pause_reason}
          </div>
        )}
      </div>

      {/* Action Footer */}
      <div
        style={{
          marginTop: 14,
          paddingTop: 10,
          borderTop: "1px solid var(--border-app)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 6,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: 4 }}>
          <Link href={`/agents/${agent.agent_id}`} className="btn btn-outline btn-sm">
            <span>Inspect</span>
            <ArrowRightIcon size={10} />
          </Link>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setModal("budget")}
            title="Edit Spend Limit"
          >
            Budget
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
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
              className="btn btn-primary btn-sm"
              style={{ backgroundColor: "var(--ok)", borderColor: "var(--ok)" }}
              onClick={() => setModal("resume")}
            >
              Resume
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-outline btn-sm"
              style={{ color: "var(--danger)", borderColor: "var(--danger-border)" }}
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
