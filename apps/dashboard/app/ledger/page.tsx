"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ArrowRightIcon } from "../../components/Icons";
import { usd } from "../../lib/api";
import type { AgentSummary, LedgerEntry } from "../../lib/types";

function LedgerContent() {
  const searchParams = useSearchParams();
  const initialAgent = searchParams ? searchParams.get("agent") || "" : "";

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>(initialAgent);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/agents")
      .then((res) => res.json())
      .then((data: AgentSummary[]) => {
        if (Array.isArray(data) && data.length > 0) {
          setAgents(data);
          const first = data[0];
          if (!selectedAgent && first) {
            setSelectedAgent(first.agent_id);
          }
        }
      })
      .catch(() => {});
  }, [selectedAgent]);

  useEffect(() => {
    if (!selectedAgent) return;
    setLoading(true);
    setError(null);
    fetch(`/api/ledger?agent_id=${encodeURIComponent(selectedAgent)}&limit=200`)
      .then((res) => {
        if (!res.ok) throw new Error("Failed to fetch ledger");
        return res.json();
      })
      .then((data) => {
        setEntries(Array.isArray(data) ? data : data.entries || []);
      })
      .catch((err) => {
        setError(err.message);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [selectedAgent]);

  return (
    <main>
      <div className="page-header">
        <div>
          <h1 className="page-title">Immutable Usage Ledger</h1>
          <p className="page-description">
            Append-only financial audit log with exact nano-USD accounting, preflight estimates, and pinned catalog versions.
          </p>
        </div>

        <Link href="/playground" className="btn btn-primary btn-sm">
          <span>Test in Playground</span>
          <ArrowRightIcon size={11} />
        </Link>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Agent Selector Bar */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md)",
          padding: "8px 12px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 16,
        }}
      >
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Agent Partition:</span>
        <select
          className="input mono"
          value={selectedAgent}
          onChange={(e) => setSelectedAgent(e.target.value)}
          style={{ width: "auto", padding: "4px 8px", fontSize: 12 }}
        >
          {agents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>
              {a.agent_id} (Spend: ${a.committed_usd} / ${a.limit_usd})
            </option>
          ))}
        </select>
      </div>

      <div
        style={{
          fontSize: 11.5,
          color: "var(--text-muted)",
          marginBottom: 16,
          paddingLeft: 2,
        }}
      >
        Append-only table. Corrections supersede earlier records rather than overwriting them.
      </div>

      {/* Ledger Table */}
      <div className="table-container">
        {loading ? (
          <div className="empty-state">Loading ledger records...</div>
        ) : entries.length === 0 ? (
          <div className="empty-state">
            No ledger entries found for agent &apos;{selectedAgent}&apos;. Execute a governed request in the Playground to generate entries.
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Kind</th>
                <th>Session</th>
                <th>Model Routing</th>
                <th>Decision</th>
                <th>Preflight In</th>
                <th>Reserved Out</th>
                <th>Actual In / Out</th>
                <th>Estimated Cost</th>
                <th>Actual Cost</th>
                <th>Catalog</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.entry_id}>
                  <td style={{ color: "var(--text-muted)", whiteSpace: "nowrap", fontSize: 11.5 }}>
                    {new Date(entry.created_at).toLocaleTimeString()}
                  </td>
                  <td>
                    <span className={`badge ${kindSeverity(entry.kind)}`}>
                      {entry.kind}
                    </span>
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: 12 }}>
                    {entry.session_id ?? "—"}
                  </td>
                  <td className="money" style={{ fontSize: 11.5 }}>
                    {entry.requested_model === entry.effective_model ? (
                      entry.effective_model
                    ) : (
                      <>
                        <s style={{ color: "var(--text-muted)" }}>
                          {entry.requested_model}
                        </s>{" "}
                        &rarr; {entry.effective_model}
                      </>
                    )}
                  </td>
                  <td>
                    <span className="badge muted">{entry.decision}</span>
                  </td>
                  <td className="money">{entry.preflight_input_tokens}</td>
                  <td className="money">{entry.reserved_output_tokens}</td>
                  <td className="money">
                    {entry.actual_input_tokens} / {entry.actual_output_tokens}
                  </td>
                  <td className="money" style={{ color: "var(--text-muted)" }}>
                    {usd(entry.estimated_max_cost_usd, 6)}
                  </td>
                  <td className="money" style={{ fontWeight: 600, color: "var(--ok)" }}>
                    {usd(entry.actual_total_cost_usd, 6)}
                  </td>
                  <td
                    className="money"
                    style={{ color: "var(--text-muted)", fontSize: 11 }}
                  >
                    {entry.price_catalog_version}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}

function kindSeverity(kind: string): string {
  switch (kind) {
    case "USAGE":
      return "ok";
    case "RELEASE":
      return "muted";
    case "PENDING_ASSUMED":
      return "warn";
    case "OVERAGE":
      return "danger";
    case "CORRECTION":
      return "info";
    default:
      return "muted";
  }
}

export default function LedgerPage() {
  return (
    <Suspense fallback={<div className="empty-state">Loading ledger...</div>}>
      <LedgerContent />
    </Suspense>
  );
}
