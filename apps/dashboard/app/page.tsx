import Link from "next/link";
import { AgentCard } from "../components/AgentCard";
import { AlertCircleIcon, ArrowRightIcon, CpuIcon, DatabaseIcon, PlusIcon, ShieldIcon } from "../components/Icons";
import { RunawayAlertBanner } from "../components/RunawayAlertBanner";
import {
  getAgents,
  getEvents,
  getLedger,
  getProviders,
  getReadiness,
  getSessions,
  getTeams,
  tokens,
  usd,
} from "../lib/api";
import type {
  AgentSummary,
  EventItem,
  LedgerEntry,
  ProviderConfig,
  Readiness,
  SessionView,
  TeamSummary,
} from "../lib/types";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  let readiness: Readiness | null = null;
  let providers: ProviderConfig[] = [];
  let teams: TeamSummary[] = [];
  let agents: AgentSummary[] = [];
  let sessions: SessionView[] = [];
  let events: EventItem[] = [];
  let ledger: LedgerEntry[] = [];
  let error: string | null = null;

  try {
    const [readRes, provRes, teamRes, agentRes, sessRes, evRes, ledRes] =
      await Promise.allSettled([
        getReadiness(),
        getProviders(),
        getTeams(),
        getAgents(),
        getSessions(),
        getEvents(20),
        getLedger(undefined, 20).catch(() => []),
      ]);

    if (readRes.status === "fulfilled") readiness = readRes.value;
    if (provRes.status === "fulfilled") providers = provRes.value;
    if (teamRes.status === "fulfilled") teams = teamRes.value;
    if (agentRes.status === "fulfilled") agents = agentRes.value;
    if (sessRes.status === "fulfilled") sessions = sessRes.value;
    if (evRes.status === "fulfilled") events = evRes.value;
    if (ledRes.status === "fulfilled") ledger = ledRes.value;
  } catch (err: any) {
    error = err.message || String(err);
  }

  const totalTeamBudget = teams.reduce((acc, t) => acc + Number(t.limit_usd || 0), 0);
  const totalCommittedSpend = teams.reduce(
    (acc, t) => acc + Number(t.committed_usd || 0),
    0,
  );
  const totalAvailable = Math.max(0, totalTeamBudget - totalCommittedSpend);

  const activeAgents = agents.filter((a) => a.status === "ACTIVE");
  const pausedAgents = agents.filter((a) => a.status.startsWith("PAUSED"));
  const warningAgents = agents.filter(
    (a) => a.utilization_percent >= 80 && a.utilization_percent < 100 && a.status === "ACTIVE",
  );
  const blockedAgents = agents.filter(
    (a) => a.utilization_percent >= 100 && a.status === "ACTIVE",
  );

  const activeSessions = sessions.filter((s) => s.status === "OPEN");

  const totalInputTokens = agents.reduce((acc, a) => acc + (a.input_tokens || 0), 0);
  const totalOutputTokens = agents.reduce((acc, a) => acc + (a.output_tokens || 0), 0);

  const productionProviders = providers.filter(
    (p) => p.is_production_ready && p.configured && p.enabled,
  );

  return (
    <main>
      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          <AlertCircleIcon size={16} />
          <span>Gateway connection error: {error}</span>
        </div>
      )}

      {/* Runaway Alert Banner */}
      <RunawayAlertBanner pausedAgents={pausedAgents} />

      {/* Production Provider Notice */}
      {productionProviders.length === 0 && (
        <div className="notice-box warning" style={{ marginBottom: 20, justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontWeight: 600, color: "var(--warn)", fontSize: 13 }}>
              Development Test Mode Active
            </div>
            <div style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 2 }}>
              Gateway is executing against deterministic test provider. Connect Amazon Bedrock, OpenAI, or Anthropic before routing production agent workloads.
            </div>
          </div>
          <Link href="/settings/providers" className="btn btn-sm">
            <span>Configure Providers</span>
            <ArrowRightIcon size={11} />
          </Link>
        </div>
      )}

      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Governance Overview</h1>
          <p className="page-description">
            Continuous pre-inference spend authorization, token bounding, and circuit breakers.
          </p>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <Link href="/agents" className="btn btn-sm">
            <PlusIcon size={12} />
            <span>Provision Agent</span>
          </Link>
          <Link href="/teams" className="btn btn-sm">
            <PlusIcon size={12} />
            <span>Create Team</span>
          </Link>
        </div>
      </div>

      {/* Unified Stats Strip */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Committed Spend</div>
          <div className="stat-value money">{usd(totalCommittedSpend, 4)}</div>
          <div className="stat-hint">of {usd(totalTeamBudget)} allocated</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Remaining Allowance</div>
          <div className="stat-value money" style={{ color: "var(--ok)" }}>
            {usd(totalAvailable, 4)}
          </div>
          <div className="stat-hint">across all scopes</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Governed Agents</div>
          <div className="stat-value money">{agents.length}</div>
          <div className="stat-hint">
            {activeAgents.length} active &bull; {pausedAgents.length} paused
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">At-Risk Agents</div>
          <div className="stat-value money" style={{ color: (warningAgents.length + blockedAgents.length) > 0 ? "var(--warn)" : "var(--text-primary)" }}>
            {warningAgents.length + blockedAgents.length}
          </div>
          <div className="stat-hint">
            {warningAgents.length} warning &bull; {blockedAgents.length} blocked
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Token Volume</div>
          <div className="stat-value money">{tokens(totalInputTokens + totalOutputTokens)}</div>
          <div className="stat-hint">
            {tokens(totalInputTokens)} in &bull; {tokens(totalOutputTokens)} out
          </div>
        </div>
      </div>

      {/* System Status Strip */}
      {readiness && (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-md)",
            padding: "10px 16px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 16,
            marginBottom: 28,
            fontSize: 12,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="pulse-dot" />
            <span style={{ fontWeight: 600 }}>Firewall Status: {readiness.status}</span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 20, color: "var(--text-secondary)" }}>
            <div>
              Store: <code className="font-mono">{(readiness.detail?.store || "memory").toUpperCase()}</code>
            </div>
            <div>
              Providers: <code className="font-mono">{readiness.detail?.providers || "test"}</code>
            </div>
            <div>
              Catalog: <code className="font-mono">{readiness.detail?.catalog_version || "2026-08-19.1"}</code>
            </div>
          </div>
        </div>
      )}

      {/* Governed Agents Section */}
      <div style={{ marginBottom: 32 }}>
        <div className="section-header">
          <span className="section-title">Governed Agents ({agents.length})</span>
          <Link href="/agents" style={{ fontSize: 12, color: "var(--primary-text)" }}>
            View all agents &rarr;
          </Link>
        </div>

        {agents.length === 0 ? (
          <div className="table-container">
            <div className="empty-state">
              No agents provisioned yet. Click &quot;Provision Agent&quot; above to establish financial governance.
            </div>
          </div>
        ) : (
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
            {agents.slice(0, 6).map((agent) => (
              <AgentCard key={agent.agent_id} agent={agent} />
            ))}
          </div>
        )}
      </div>

      {/* Two-Column Bottom Split: Recent Events & Recent Ledger */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Recent Governance Events */}
        <div>
          <div className="section-header">
            <span className="section-title">Recent Governance Events</span>
            <Link href="/events" style={{ fontSize: 12, color: "var(--primary-text)" }}>
              View all &rarr;
            </Link>
          </div>

          <div className="table-container">
            {events.length === 0 ? (
              <div className="empty-state">No threshold or governance events recorded.</div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Type</th>
                    <th>Target</th>
                    <th>Description</th>
                  </tr>
                </thead>
                <tbody>
                  {events.slice(0, 5).map((ev) => (
                    <tr key={ev.event_id}>
                      <td style={{ color: "var(--text-muted)", fontSize: 11.5, whiteSpace: "nowrap" }}>
                        {new Date(ev.occurred_at).toLocaleTimeString()}
                      </td>
                      <td>
                        <span className={`badge ${ev.severity || "muted"}`}>
                          {ev.kind.replace("_", " ")}
                        </span>
                      </td>
                      <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>
                        {ev.agent_id || ev.team_id || "System"}
                      </td>
                      <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                        {ev.description}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Recent Settled Inferences */}
        <div>
          <div className="section-header">
            <span className="section-title">Recent Settled Inferences</span>
            <Link href="/ledger" style={{ fontSize: 12, color: "var(--primary-text)" }}>
              View ledger &rarr;
            </Link>
          </div>

          <div className="table-container">
            {ledger.length === 0 ? (
              <div className="empty-state">No inference records yet. Execute a prompt in Playground.</div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Model</th>
                    <th>Decision</th>
                    <th>Actual Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.slice(0, 5).map((entry) => (
                    <tr key={entry.entry_id}>
                      <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>
                        {entry.agent_id}
                      </td>
                      <td className="money" style={{ fontSize: 12 }}>
                        {entry.effective_model}
                        {entry.requested_model !== entry.effective_model && (
                          <span className="badge info" style={{ marginLeft: 6, fontSize: 10 }}>
                            sub
                          </span>
                        )}
                      </td>
                      <td>
                        <span className="badge muted">{entry.decision}</span>
                      </td>
                      <td className="money" style={{ color: "var(--ok)", fontWeight: 500 }}>
                        {usd(entry.actual_total_cost_usd, 4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
