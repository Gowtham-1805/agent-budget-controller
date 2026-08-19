"use client";

import Link from "next/link";
import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { AlertTriangleIcon, ArrowRightIcon } from "./Icons";
import { ResumeAgentModal } from "./ResumeAgentModal";

interface RunawayAlertBannerProps {
  pausedAgents: AgentSummary[];
  onAgentResumed?: () => void;
}

export function RunawayAlertBanner({
  pausedAgents,
  onAgentResumed,
}: RunawayAlertBannerProps) {
  const [resumingAgent, setResumingAgent] = useState<AgentSummary | null>(null);

  const runawayAgents = pausedAgents.filter(
    (a) => a.status === "PAUSED_RUNAWAY" || a.review_required,
  );

  if (runawayAgents.length === 0) return null;

  return (
    <>
      <div className="notice-box danger" style={{ marginBottom: 20, flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, width: "100%" }}>
          <AlertTriangleIcon size={18} style={{ color: "var(--danger)", flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, color: "var(--danger)", fontSize: 13 }}>
              Runaway Agent Velocity Circuit Tripped
            </div>
            <div style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 2 }}>
              {runawayAgents.length} {runawayAgents.length === 1 ? "agent consumed" : "agents consumed"} &gt;20% of periodic budget in a rolling hour. New inference calls are intercepted at the gateway with zero provider spend incurred.
            </div>
          </div>
        </div>

        <div
          style={{
            marginTop: 12,
            width: "100%",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 8,
          }}
        >
          {runawayAgents.map((agent) => (
            <div
              key={agent.agent_id}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                padding: "8px 12px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
              }}
            >
              <div>
                <div style={{ fontWeight: 600, fontSize: 13 }}>
                  <Link href={`/agents/${agent.agent_id}`} style={{ color: "var(--text-primary)" }}>
                    {agent.agent_id}
                  </Link>
                  <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 6 }}>
                    Team: {agent.team_id}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 2 }}>
                  {agent.pause_reason || "Hourly velocity threshold exceeded"}
                </div>
              </div>

              <div style={{ display: "flex", gap: 6 }}>
                <Link href={`/agents/${agent.agent_id}`} className="btn btn-sm">
                  <span>Review</span>
                  <ArrowRightIcon size={11} />
                </Link>
                <button
                  type="button"
                  className="btn btn-success btn-sm"
                  onClick={() => setResumingAgent(agent)}
                >
                  Resume
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {resumingAgent && (
        <ResumeAgentModal
          agent={resumingAgent}
          onClose={() => setResumingAgent(null)}
          onSuccess={() => {
            setResumingAgent(null);
            onAgentResumed?.();
          }}
        />
      )}
    </>
  );
}
