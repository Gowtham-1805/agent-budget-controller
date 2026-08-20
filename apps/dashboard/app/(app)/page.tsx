"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MetricCard } from "@/components/MetricCard";
import { CreateAgentModal } from "@/components/CreateAgentModal";
import { CreateTeamModal } from "@/components/CreateTeamModal";
import {
  TrendingUpIcon,
  ShieldIcon,
  CpuIcon,
  AlertCircleIcon,
  ArrowRightIcon,
  SettingsIcon,
  TerminalIcon,
  ActivityIcon,
  PlusIcon,
  RefreshCwIcon,
  ClockIcon,
  BookOpenIcon,
} from "@/components/Icons";
import { RunawayAlertBanner } from "@/components/RunawayAlertBanner";
import { tokens, usd } from "@/lib/format";
import { useSession } from "@/lib/session-context";
import type {
  AgentSummary,
  EventItem,
  LedgerEntry,
  ProviderConfig,
  Readiness,
  SessionView,
  TeamSummary,
} from "@/lib/types";

export default function OverviewPage() {
  const session = useSession();
  const canWrite = session.role === "OPERATOR" || session.role === "ADMIN";
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [creatingAgent, setCreatingAgent] = useState(false);
  const [creatingTeam, setCreatingTeam] = useState(false);

  function loadData() {
    setLoading(true);
    setError(null);
    Promise.allSettled([
      fetch("/api/readyz").then((r) => r.json()),
      fetch("/api/providers").then((r) => r.json()),
      fetch("/api/teams").then((r) => r.json()),
      fetch("/api/agents").then((r) => r.json()),
      fetch("/api/sessions").then((r) => r.json()),
      fetch("/api/events?limit=20").then((r) => r.json()),
    ])
      .then(async ([readRes, provRes, teamRes, agentRes, sessRes, evRes]) => {
        if (readRes.status === "fulfilled") setReadiness(readRes.value);
        if (provRes.status === "fulfilled" && Array.isArray(provRes.value)) setProviders(provRes.value);
        if (teamRes.status === "fulfilled" && Array.isArray(teamRes.value)) setTeams(teamRes.value);
        if (agentRes.status === "fulfilled" && Array.isArray(agentRes.value)) {
          const loadedAgents = agentRes.value;
          setAgents(loadedAgents);
          // Query partitioned ledger records for the active agents
          const topAgents = loadedAgents.slice(0, 4);
          const ledgerPromises = topAgents.map((a) =>
            fetch(`/api/ledger?agent_id=${encodeURIComponent(a.agent_id)}&limit=10`)
              .then((r) => (r.ok ? r.json() : []))
              .then((data) => (Array.isArray(data) ? data : data.entries || []))
              .catch(() => [])
          );
          const ledgerResults = await Promise.allSettled(ledgerPromises);
          const combined = ledgerResults
            .filter((r): r is PromiseFulfilledResult<LedgerEntry[]> => r.status === "fulfilled")
            .flatMap((r) => r.value)
            .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
          setLedger(combined);
        }
        if (sessRes.status === "fulfilled" && Array.isArray(sessRes.value)) setSessions(sessRes.value);
        if (evRes.status === "fulfilled" && Array.isArray(evRes.value)) setEvents(evRes.value);
      })
      .catch((err) => {
        setError(err.message || "Failed to load dashboard data");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadData();
  }, []);

  const totalTeamBudget = teams.reduce((acc, t) => acc + Number(t.limit_usd || 0), 0);
  const totalCommittedSpend = teams.reduce(
    (acc, t) => acc + Number(t.committed_usd || 0),
    0,
  );
  const totalReserved = agents.reduce((acc, a) => acc + Number(a.reserved_usd || 0), 0);
  const totalAvailable = Math.max(0, totalTeamBudget - totalCommittedSpend);

  const activeAgents = agents.filter((a) => a.status === "ACTIVE");
  const pausedAgents = agents.filter((a) => a.status.startsWith("PAUSED"));
  const warningAgents = agents.filter(
    (a) => a.utilization_percent >= 80 && a.utilization_percent < 100 && a.status === "ACTIVE",
  );
  const blockedAgents = agents.filter(
    (a) => a.utilization_percent >= 100 && a.status === "ACTIVE",
  );


  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Overview Action Bar */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.01em" }}>
            Real-Time AI Spend &amp; Governance Overview
          </h2>
          <p style={{ fontSize: 12.5, color: "var(--text-secondary)", marginTop: 2 }}>
            Financial authorization telemetry, budget utilization rails, and model distribution.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={loadData}
            title="Refresh Live Metrics"
          >
            <RefreshCwIcon size={13} />
            <span>{loading ? "Refreshing..." : "Refresh"}</span>
          </button>

          {canWrite && (
            <>
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => setCreatingTeam(true)}
              >
                <PlusIcon size={13} />
                <span>New Team</span>
              </button>

              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => setCreatingAgent(true)}
              >
                <PlusIcon size={13} />
                <span>New Agent</span>
              </button>
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>Gateway connection error: {error}</span>
        </div>
      )}

      {/* Runaway Alert Banner */}
      <RunawayAlertBanner pausedAgents={pausedAgents} />

      {/* -------------------------------------------------------------------- */}
      {/* 1. Top 4 Metric KPI Cards (Matching Reference Layout)                 */}
      {/* -------------------------------------------------------------------- */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
        <MetricCard
          label="Total Committed Spend"
          value={usd(totalCommittedSpend, 4)}
          subValue={`of ${usd(totalTeamBudget, 2)} budget`}
          trend={{ value: `${((totalCommittedSpend / (totalTeamBudget || 1)) * 100).toFixed(0)}% utilized`, isNeutral: true }}
          colorTheme="coral"
          icon={TrendingUpIcon}
          sparklineData={[12, 18, 25, 20, 32, 45, 60, 78]}
        />

        <MetricCard
          label="In-Flight Reserved Hold"
          value={usd(totalReserved, 4)}
          subValue="Atomically locked pre-inference"
          trend={{ value: `${agents.filter(a => Number(a.reserved_usd || 0) > 0).length} In-Flight`, isPositive: true }}
          colorTheme="cyan"
          icon={ShieldIcon}
          sparklineData={[5, 12, 8, 15, 6, 20, 4, 10]}
        />

        <MetricCard
          label="Governed Agents"
          value={`${activeAgents.length} Active`}
          subValue={`${pausedAgents.length} paused · ${teams.length} teams`}
          trend={{ value: `${agents.length} Total`, isPositive: true }}
          colorTheme="indigo"
          icon={CpuIcon}
          sparklineData={[2, 3, 3, 4, 4, 5, 5, 6]}
        />
      </div>


      {/* -------------------------------------------------------------------- */}
      {/* 3. Bottom Grid: Top Active Agents Table (2/3) + Provider Status (1/3)  */}
      {/* -------------------------------------------------------------------- */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 20 }}>
        {/* Left: Top Governed Agents Table */}
        <div className="shadcn-card" style={{ display: "flex", flexDirection: "column" }}>
          <div className="card-header">
            <div>
              <div className="card-title">Top Governed Agents</div>
              <div className="card-subtitle">Active agents ranked by real-time budget utilization</div>
            </div>
            <Link href="/agents" className="btn btn-outline btn-sm">
              <span>View All ({agents.length})</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>

          <div className="table-container" style={{ border: "none", borderRadius: "var(--radius-md)" }}>
            <table className="shadcn-table">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Team</th>
                  <th>Spend / Limit</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th style={{ textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {agents.length === 0 ? (
                  <tr>
                    <td colSpan={6} style={{ textAlign: "center", color: "var(--text-muted)", padding: "28px" }}>
                      {loading ? "Loading agents..." : "No agents provisioned yet."}
                    </td>
                  </tr>
                ) : (
                  agents.slice(0, 5).map((a) => {
                    const isWarning = a.utilization_percent >= 80 && a.utilization_percent < 100;
                    const isBlocked = a.utilization_percent >= 100;
                    const isPaused = a.status.startsWith("PAUSED");

                    return (
                      <tr key={a.agent_id}>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <div
                              style={{
                                width: 28,
                                height: 28,
                                borderRadius: "var(--radius-sm)",
                                backgroundColor: "var(--bg-muted)",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                color: "var(--text-secondary)",
                              }}
                            >
                              <CpuIcon size={14} />
                            </div>
                            <div>
                              <Link
                                href={`/agents/${a.agent_id}`}
                                style={{
                                  fontWeight: 600,
                                  color: "var(--text-primary)",
                                  textDecoration: "none",
                                  fontSize: 13,
                                }}
                              >
                                {a.agent_id}
                              </Link>
                              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                                {a.window_type} window
                              </div>
                            </div>
                          </div>
                        </td>
                        <td>
                          <span className="badge badge-neutral" style={{ fontSize: 11 }}>
                            {a.team_id}
                          </span>
                        </td>
                        <td style={{ minWidth: 140 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
                            <span className="money" style={{ fontWeight: 600 }}>{usd(a.committed_usd, 4)}</span>
                            <span className="money" style={{ color: "var(--text-muted)" }}>{usd(a.limit_usd, 2)}</span>
                          </div>
                          <div className="meter-rail">
                            <div
                              className={`meter-fill ${isBlocked ? "danger" : isWarning ? "warn" : "ok"}`}
                              style={{ width: `${Math.min(100, Math.max(2, a.utilization_percent))}%` }}
                            />
                          </div>
                        </td>
                        <td>
                          <code style={{ fontSize: 11, backgroundColor: "var(--bg-muted)", padding: "2px 6px", borderRadius: 4 }}>
                            {a.preferred_model}
                          </code>
                        </td>
                        <td>
                          <span
                            className={`badge ${
                              isPaused ? "badge-danger" : isBlocked ? "badge-danger" : isWarning ? "badge-warning" : "badge-ok"
                            }`}
                          >
                            {a.status}
                          </span>
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <Link href={`/agents/${a.agent_id}`} className="btn btn-ghost btn-sm">
                            Inspect
                          </Link>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right: Provider Health & Quick Simulator */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="shadcn-card">
            <div className="card-header" style={{ marginBottom: 12 }}>
              <div>
                <div className="card-title">Provider Health</div>
                <div className="card-subtitle">Inference adapters &amp; status</div>
              </div>
              <Link href="/settings/providers" className="btn btn-ghost btn-sm">
                <SettingsIcon size={13} />
              </Link>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {providers.map((p) => (
                <div
                  key={p.provider}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "8px 10px",
                    backgroundColor: "var(--bg-app)",
                    border: "1px solid var(--border-app)",
                    borderRadius: "var(--radius-md)",
                    fontSize: 12.5,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "9999px",
                        backgroundColor: p.enabled ? "var(--ok)" : "var(--text-muted)",
                      }}
                    />
                    <span style={{ fontWeight: 600, color: "var(--text-primary)", textTransform: "capitalize" }}>
                      {p.display_name || p.provider}
                    </span>
                  </div>
                  <span
                    className={`badge ${p.configured ? "badge-ok" : "badge-neutral"}`}
                    style={{ fontSize: 10.5 }}
                  >
                    {p.configured ? "Ready" : "Not Set"}
                  </span>
                </div>
              ))}
            </div>

            <div style={{ borderTop: "1px solid var(--border-app)", marginTop: 12, paddingTop: 10, fontSize: 11.5, color: "var(--text-muted)", display: "flex", justifyContent: "space-between" }}>
              <span>Catalog Version:</span>
              <strong style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
                {readiness?.detail?.catalog_version || "unknown"}
              </strong>
            </div>
          </div>

          {/* Quick Simulation Link Card */}
          <div
            className="shadcn-card"
            style={{
              backgroundColor: "var(--primary)",
              color: "#ffffff",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
            }}
          >
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
                <TerminalIcon size={16} />
                <span>Simulate Inference</span>
              </div>
              <p style={{ fontSize: 12, color: "rgba(255,255,255,0.8)", marginTop: 4 }}>
                Test atomic reservations, model substitutions, and zero-spend hard blocks in real time.
              </p>
            </div>
            <Link
              href="/playground"
              className="btn btn-sm"
              style={{
                backgroundColor: "#ffffff",
                color: "var(--primary)",
                marginTop: 14,
                width: "fit-content",
                fontWeight: 600,
              }}
            >
              <span>Open Playground</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>
        </div>
      </div>

      {/* -------------------------------------------------------------------- */}
      {/* 4. Split Feed: Recent Governance Events (1/2) & Settled Ledger (1/2)  */}
      {/* -------------------------------------------------------------------- */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Recent Governance Events */}
        <div className="shadcn-card">
          <div className="card-header">
            <div>
              <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <ActivityIcon size={15} />
                <span>Recent Governance Events</span>
              </div>
              <div className="card-subtitle">Warnings, blocks &amp; breaker events</div>
            </div>
            <Link href="/events" className="btn btn-outline btn-sm">
              <span>View All</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {events.length === 0 ? (
              <div className="empty-state" style={{ padding: 24 }}>No events recorded yet.</div>
            ) : (
              events.slice(0, 4).map((e) => (
                <div
                  key={e.event_id}
                  style={{
                    padding: "8px 12px",
                    backgroundColor: "var(--bg-app)",
                    border: "1px solid var(--border-app)",
                    borderRadius: "var(--radius-md)",
                    fontSize: 12,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <div>
                    <strong style={{ color: "var(--text-primary)" }}>{e.title}</strong>
                    <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 1 }}>
                      {e.agent_id ? `Agent: ${e.agent_id}` : e.team_id ? `Team: ${e.team_id}` : "System"}
                    </div>
                  </div>
                  <span className={`badge ${e.severity === "danger" ? "badge-danger" : e.severity === "warn" ? "badge-warning" : "badge-ok"}`} style={{ fontSize: 10.5 }}>
                    {e.severity.toUpperCase()}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Settled Inference Ledger Feed */}
        <div className="shadcn-card">
          <div className="card-header">
            <div>
              <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <BookOpenIcon size={15} />
                <span>Settled Inferences (Ledger)</span>
              </div>
              <div className="card-subtitle">Live immutable audit trail</div>
            </div>
            <Link href="/ledger" className="btn btn-outline btn-sm">
              <span>View Ledger</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {ledger.length === 0 ? (
              <div className="empty-state" style={{ padding: 24 }}>No inferences logged yet.</div>
            ) : (
              ledger.slice(0, 4).map((entry) => (
                <div
                  key={entry.entry_id}
                  style={{
                    padding: "8px 12px",
                    backgroundColor: "var(--bg-app)",
                    border: "1px solid var(--border-app)",
                    borderRadius: "var(--radius-md)",
                    fontSize: 12,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <div>
                    <span className="font-mono" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                      {entry.request_id}
                    </span>
                    <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 1 }}>
                      {entry.effective_model} &bull; {entry.actual_input_tokens + entry.actual_output_tokens} tokens
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <span className="money" style={{ fontWeight: 600, color: "var(--ok)" }}>
                      {usd(entry.actual_total_cost_usd, 4)}
                    </span>
                    <div className="badge badge-ok" style={{ fontSize: 9.5, padding: "0 4px", display: "block", marginTop: 2 }}>
                      {entry.decision}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Interactive Modals */}
      {creatingAgent && (
        <CreateAgentModal
          onClose={() => setCreatingAgent(false)}
          onSuccess={() => {
            setCreatingAgent(false);
            loadData();
          }}
        />
      )}

      {creatingTeam && (
        <CreateTeamModal
          onClose={() => setCreatingTeam(false)}
          onSuccess={() => {
            setCreatingTeam(false);
            loadData();
          }}
        />
      )}
    </div>
  );
}
