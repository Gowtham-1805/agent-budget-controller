"use client";

import Link from "next/link";
import { useState } from "react";
import type { AgentSummary } from "../lib/types";
import { AlertTriangleIcon, ArrowRightIcon, ShieldIcon } from "./Icons";
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
      <div
        className="shadcn-card"
        style={{
          borderLeft: "4px solid var(--danger)",
          backgroundColor: "var(--danger-soft)",
          borderColor: "var(--danger-border)",
          padding: "16px 20px",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: "9999px",
                backgroundColor: "var(--danger)",
                color: "#ffffff",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
                marginTop: 2,
              }}
            >
              <AlertTriangleIcon size={18} />
            </div>
            <div>
              <div style={{ fontWeight: 700, color: "var(--danger)", fontSize: 14 }}>
                Runaway Agent Velocity Circuit Tripped
              </div>
              <div style={{ color: "var(--text-secondary)", fontSize: 12.5, marginTop: 2 }}>
                {runawayAgents.length} {runawayAgents.length === 1 ? "agent has" : "agents have"} exceeded the &gt;20% rolling hourly consumption limit. Outbound inference requests are currently blocked at the gateway with zero LLM provider spend.
              </div>
            </div>
          </div>

          <span className="badge badge-danger" style={{ fontSize: 11 }}>
            {runawayAgents.length} ISOLATED
          </span>
        </div>

        {/* Affected Agents Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 10,
          }}
        >
          {runawayAgents.map((agent) => (
            <div
              key={agent.agent_id}
              style={{
                backgroundColor: "#ffffff",
                border: "1px solid var(--danger-border)",
                borderRadius: "var(--radius-md)",
                padding: "10px 14px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 10,
                boxShadow: "var(--shadow-sm)",
              }}
            >
              <div>
                <div style={{ fontWeight: 700, fontSize: 13 }}>
                  <Link
                    href={`/agents/${agent.agent_id}`}
                    style={{ color: "var(--text-primary)", textDecoration: "none" }}
                  >
                    {agent.agent_id}
                  </Link>
                  <span className="badge badge-neutral" style={{ marginLeft: 6, fontSize: 10 }}>
                    {agent.team_id}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 2, fontWeight: 500 }}>
                  {agent.pause_reason || "Hourly velocity threshold exceeded"}
                </div>
              </div>

              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <Link
                  href={`/agents/${agent.agent_id}`}
                  className="btn btn-outline btn-sm"
                  style={{ fontSize: 11, padding: "4px 8px" }}
                >
                  Review
                </Link>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  style={{ backgroundColor: "var(--ok)", borderColor: "var(--ok)", fontSize: 11, padding: "4px 10px" }}
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
