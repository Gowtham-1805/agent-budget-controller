"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { RefreshCwIcon } from "../../components/Icons";
import { ResumeAgentModal } from "../../components/ResumeAgentModal";
import type { AgentSummary, EventItem } from "../../lib/types";

export default function EventsPage() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [filterType, setFilterType] = useState<string>("ALL");
  const [loading, setLoading] = useState(true);
  const [resumingAgent, setResumingAgent] = useState<AgentSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  function loadEvents() {
    setLoading(true);
    Promise.allSettled([
      fetch("/api/events?limit=200").then((r) => r.json()),
      fetch("/api/agents").then((r) => r.json()),
    ])
      .then(([evRes, agRes]) => {
        if (evRes.status === "fulfilled" && Array.isArray(evRes.value)) {
          setEvents(evRes.value);
        }
        if (agRes.status === "fulfilled" && Array.isArray(agRes.value)) {
          setAgents(agRes.value);
        }
      })
      .catch((err) => {
        setError(err.message);
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadEvents();
  }, []);

  const filteredEvents = events.filter((e) => {
    if (filterType === "RUNAWAY" && !e.kind.includes("RUNAWAY")) return false;
    if (filterType === "WARNING" && !e.kind.includes("80")) return false;
    if (filterType === "BLOCK" && !e.kind.includes("100") && !e.kind.includes("BLOCK")) return false;
    if (filterType === "AUDIT" && !e.kind.includes("AUDIT")) return false;
    return true;
  });

  const runawayEvents = events.filter((e) => e.kind.includes("RUNAWAY"));

  return (
    <main>
      <div className="page-header">
        <div>
          <h1 className="page-title">Governance Events &amp; Audit Trail</h1>
          <p className="page-description">
            Immutable chronological log of threshold crossings, runaway circuit trips, and administrative actions.
          </p>
        </div>

        <button type="button" className="btn btn-sm" onClick={loadEvents}>
          <RefreshCwIcon size={12} />
          <span>Refresh Feed</span>
        </button>
      </div>

      {error && (
        <div className="notice-box danger" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Segmented Filter Bar */}
      <div
        style={{
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          marginBottom: 16,
        }}
      >
        <button
          type="button"
          className={`btn btn-sm ${filterType === "ALL" ? "btn-primary" : "btn-ghost"}`}
          onClick={() => setFilterType("ALL")}
        >
          All Events ({events.length})
        </button>
        <button
          type="button"
          className={`btn btn-sm ${filterType === "RUNAWAY" ? "btn-danger" : "btn-ghost"}`}
          onClick={() => setFilterType("RUNAWAY")}
        >
          Runaway Incidents ({runawayEvents.length})
        </button>
        <button
          type="button"
          className={`btn btn-sm ${filterType === "WARNING" ? "btn-primary" : "btn-ghost"}`}
          onClick={() => setFilterType("WARNING")}
        >
          80% Warnings
        </button>
        <button
          type="button"
          className={`btn btn-sm ${filterType === "BLOCK" ? "btn-danger" : "btn-ghost"}`}
          onClick={() => setFilterType("BLOCK")}
        >
          100% Hard Blocks
        </button>
        <button
          type="button"
          className={`btn btn-sm ${filterType === "AUDIT" ? "btn-primary" : "btn-ghost"}`}
          onClick={() => setFilterType("AUDIT")}
        >
          Admin Audits
        </button>
      </div>

      {/* Event Table */}
      <div className="table-container">
        {loading ? (
          <div className="empty-state">Loading governance events...</div>
        ) : filteredEvents.length === 0 ? (
          <div className="empty-state">No events found in this category.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Category</th>
                <th>Target Scope</th>
                <th>Event Summary</th>
                <th>Details</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredEvents.map((ev) => {
                const targetAgent = agents.find((a) => a.agent_id === ev.agent_id);
                const isRunaway = ev.kind.includes("RUNAWAY");
                const isPaused = targetAgent?.status.startsWith("PAUSED");

                return (
                  <tr key={ev.event_id}>
                    <td style={{ color: "var(--text-muted)", whiteSpace: "nowrap", fontSize: 11.5 }}>
                      {new Date(ev.occurred_at).toLocaleString()}
                    </td>
                    <td>
                      <span className={`badge ${ev.severity}`}>
                        {ev.kind.replace("_", " ")}
                      </span>
                    </td>
                    <td style={{ fontWeight: 500 }}>
                      {ev.agent_id ? (
                        <Link href={`/agents/${ev.agent_id}`} style={{ color: "var(--primary-text)" }}>
                          {ev.agent_id}
                        </Link>
                      ) : ev.team_id ? (
                        <Link href={`/teams/${ev.team_id}`}>Team: {ev.team_id}</Link>
                      ) : (
                        "System"
                      )}
                    </td>
                    <td style={{ fontSize: 12.5, color: "var(--text-primary)" }}>{ev.description}</td>
                    <td style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                      {ev.actor ? `Actor: ${ev.actor}` : ev.amount_usd ? `Spend: $${ev.amount_usd}` : "—"}
                    </td>
                    <td>
                      {isRunaway && isPaused && targetAgent && (
                        <button
                          type="button"
                          className="btn btn-success btn-sm"
                          onClick={() => setResumingAgent(targetAgent)}
                        >
                          Resume
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {resumingAgent && (
        <ResumeAgentModal
          agent={resumingAgent}
          onClose={() => setResumingAgent(null)}
          onSuccess={() => {
            setResumingAgent(null);
            loadEvents();
          }}
        />
      )}
    </main>
  );
}
