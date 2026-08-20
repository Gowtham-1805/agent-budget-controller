"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import {
  ArrowRightIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldIcon,
  TerminalIcon,
  CpuIcon,
  BookOpenIcon,
  CheckCircleIcon,
} from "@/components/Icons";
import { usd } from "@/lib/format";
import type { AgentSummary, LedgerEntry } from "@/lib/types";

function LedgerContent() {
  const searchParams = useSearchParams();
  const initialAgent = searchParams ? searchParams.get("agent") || "" : "";

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>(initialAgent);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/agents")
      .then((res) => res.json())
      .then((data: AgentSummary[]) => {
        if (Array.isArray(data) && data.length > 0) {
          setAgents(data);
          if (!selectedAgent && data[0]) {
            setSelectedAgent(data[0].agent_id);
          }
        }
      })
      .catch(() => {});
  }, [selectedAgent]);

  function loadLedger(agentId: string) {
    if (!agentId) return;
    setLoading(true);
    setError(null);
    fetch(`/api/ledger?agent_id=${encodeURIComponent(agentId)}&limit=200`)
      .then((res) => {
        if (!res.ok) throw new Error("Failed to fetch ledger");
        return res.json();
      })
      .then((data) => {
        setEntries(Array.isArray(data) ? data : data.entries || []);
      })
      .catch((err) => {
        setError(err.message || "Failed to load ledger records");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    if (selectedAgent) {
      loadLedger(selectedAgent);
    }
  }, [selectedAgent]);

  const currentAgent = agents.find((a) => a.agent_id === selectedAgent);

  // Compute summary stats for the current agent's ledger
  const totalSettledCost = entries.reduce((acc, e) => acc + Number(e.actual_total_cost_usd || 0), 0);
  const totalTokens = entries.reduce((acc, e) => acc + (e.actual_input_tokens || 0) + (e.actual_output_tokens || 0), 0);
  const substitutedCount = entries.filter((e) => e.requested_model !== e.effective_model).length;

  const filteredEntries = entries.filter((entry) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      entry.entry_id.toLowerCase().includes(q) ||
      (entry.request_id && entry.request_id.toLowerCase().includes(q)) ||
      (entry.session_id && entry.session_id.toLowerCase().includes(q)) ||
      entry.effective_model.toLowerCase().includes(q) ||
      entry.decision.toLowerCase().includes(q) ||
      entry.kind.toLowerCase().includes(q)
    );
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">Immutable Usage Ledger</h1>
          <p className="page-description">
            Append-only financial audit log with exact nano-USD accounting, preflight estimates, and pinned catalog versions.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => loadLedger(selectedAgent)}
          >
            <RefreshCwIcon size={12} />
            <span>{loading ? "Refreshing..." : "Refresh"}</span>
          </button>

          <Link href="/playground" className="btn btn-primary btn-sm">
            <TerminalIcon size={12} />
            <span>Simulate in Playground</span>
          </Link>
        </div>
      </div>

      {error && (
        <div className="notice-box danger">
          <span>{error}</span>
        </div>
      )}

      {/* 4-Metric Summary Strip for Selected Agent */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Settled Audit Records</div>
          <div className="stat-value money">{entries.length}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Total Settled Spend</div>
          <div className="stat-value money" style={{ color: "var(--ok)" }}>
            {usd(totalSettledCost, 6)}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Metered Token Volume</div>
          <div className="stat-value money">
            {totalTokens.toLocaleString()}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-label">Model Substitutions</div>
          <div className="stat-value money" style={{ color: "var(--brand-blue)" }}>
            {substitutedCount}
          </div>
        </div>
      </div>

      {/* Agent Partition Selector & Search Filter Bar */}
      <div className="shadcn-card" style={{ padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <CpuIcon size={14} className="text-secondary" />
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Agent Partition:</span>
          </div>
          <select
            className="form-select font-mono"
            value={selectedAgent}
            onChange={(e) => setSelectedAgent(e.target.value)}
            style={{ width: "auto", minWidth: 260, fontSize: 12.5 }}
          >
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.agent_id} (Team: {a.team_id}, Spend: ${a.committed_usd} / ${a.limit_usd})
              </option>
            ))}
          </select>

          {currentAgent && (
            <span className="badge badge-neutral" style={{ fontSize: 11 }}>
              Limit: ${currentAgent.limit_usd} &bull; {currentAgent.window_type}
            </span>
          )}
        </div>

        {/* Search Input */}
        <div style={{ position: "relative", minWidth: 220 }}>
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}>
            <SearchIcon size={13} />
          </span>
          <input
            type="text"
            className="form-input font-mono"
            placeholder="Search records..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{ paddingLeft: 30, fontSize: 12.5 }}
          />
        </div>
      </div>

      {/* Ledger Table */}
      <div className="shadcn-card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="table-container" style={{ border: "none" }}>
          {loading ? (
            <div className="empty-state">Loading immutable ledger entries...</div>
          ) : filteredEntries.length === 0 ? (
            <div className="empty-state">
              No ledger entries found for agent &apos;{selectedAgent}&apos;. Execute a governed request in the Playground to generate entries.
            </div>
          ) : (
            <table className="shadcn-table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Kind</th>
                  <th>Request / Session</th>
                  <th>Model Routing</th>
                  <th>Decision</th>
                  <th>Preflight Tokens</th>
                  <th>Actual Tokens</th>
                  <th>Estimated Hold</th>
                  <th>Settled Cost</th>
                  <th style={{ textAlign: "right" }}>Price Catalog</th>
                </tr>
              </thead>
              <tbody>
                {filteredEntries.map((entry) => (
                  <tr key={entry.entry_id}>
                    <td style={{ color: "var(--text-muted)", whiteSpace: "nowrap", fontSize: 11.5 }}>
                      {new Date(entry.created_at).toLocaleTimeString()}
                    </td>

                    <td>
                      <span className={`badge ${kindBadgeClass(entry.kind)}`} style={{ fontSize: 10.5 }}>
                        {entry.kind}
                      </span>
                    </td>

                    <td>
                      <div>
                        <code style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-primary)" }}>
                          {entry.request_id || entry.entry_id.slice(0, 14)}
                        </code>
                        {entry.session_id && (
                          <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 1 }}>
                            Session: {entry.session_id}
                          </div>
                        )}
                      </div>
                    </td>

                    <td>
                      {entry.requested_model === entry.effective_model ? (
                        <code style={{ fontSize: 11.5, backgroundColor: "var(--bg-muted)", padding: "2px 6px", borderRadius: 4 }}>
                          {entry.effective_model}
                        </code>
                      ) : (
                        <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11.5 }}>
                          <s style={{ color: "var(--text-muted)" }}>{entry.requested_model}</s>
                          <span style={{ color: "var(--brand-blue)", fontWeight: 600 }}>&rarr; {entry.effective_model}</span>
                        </div>
                      )}
                    </td>

                    <td>
                      <span className={`badge ${entry.decision === "ALLOWED" ? "badge-ok" : entry.decision.includes("SUBSTITUTED") ? "badge-indigo" : "badge-neutral"}`} style={{ fontSize: 10.5 }}>
                        {entry.decision}
                      </span>
                    </td>

                    <td className="money" style={{ fontSize: 12 }}>
                      {entry.preflight_input_tokens} in / {entry.reserved_output_tokens} out
                    </td>

                    <td className="money" style={{ fontSize: 12 }}>
                      {entry.actual_input_tokens} in / {entry.actual_output_tokens} out
                    </td>

                    <td className="money" style={{ color: "var(--text-muted)", fontSize: 12 }}>
                      {usd(entry.estimated_max_cost_usd, 6)}
                    </td>

                    <td className="money" style={{ fontWeight: 700, color: "var(--ok)", fontSize: 12.5 }}>
                      {usd(entry.actual_total_cost_usd, 6)}
                    </td>

                    <td style={{ textAlign: "right" }}>
                      <code style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {entry.price_catalog_version}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function kindBadgeClass(kind: string): string {
  switch (kind) {
    case "USAGE":
      return "badge-ok";
    case "RELEASE":
      return "badge-neutral";
    case "PENDING_ASSUMED":
      return "badge-warning";
    case "OVERAGE":
      return "badge-danger";
    case "CORRECTION":
      return "badge-indigo";
    default:
      return "badge-neutral";
  }
}

export default function LedgerPage() {
  return (
    <Suspense fallback={<div className="shadcn-card" style={{ textAlign: "center", padding: 48 }}>Loading ledger...</div>}>
      <LedgerContent />
    </Suspense>
  );
}
