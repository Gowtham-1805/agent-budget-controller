"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  RefreshCwIcon,
  AlertTriangleIcon,
  CheckCircleIcon,
  AlertCircleIcon,
  ShieldIcon,
  SearchIcon,
  ArrowRightIcon,
  CpuIcon,
} from "@/components/Icons";
import { ResumeAgentModal } from "@/components/ResumeAgentModal";
import type { AgentSummary, EventItem } from "@/lib/types";

export default function EventsPage() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [filterType, setFilterType] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [resumingAgent, setResumingAgent] = useState<AgentSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  function loadEvents() {
    setLoading(true);
    setError(null);
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
        setError(err.message || "Failed to load events");
      })
      .finally(() => {
        setLoading(false);
      });
  }

  useEffect(() => {
    loadEvents();
  }, []);

  const runawayEvents = events.filter((e) => e.kind.includes("RUNAWAY"));
  const warningEvents = events.filter((e) => e.kind.includes("80") || e.severity === "warn");
  const blockEvents = events.filter((e) => e.kind.includes("100") || e.kind.includes("BLOCK") || e.severity === "danger");
  const auditEvents = events.filter((e) => e.kind.includes("AUDIT") || e.kind.includes("ADMIN"));

  const filteredEvents = events.filter((e) => {
    if (filterType === "RUNAWAY" && !e.kind.includes("RUNAWAY")) return false;
    if (filterType === "WARNING" && !e.kind.includes("80") && e.severity !== "warn") return false;
    if (filterType === "BLOCK" && !e.kind.includes("100") && !e.kind.includes("BLOCK") && e.severity !== "danger") return false;
    if (filterType === "AUDIT" && !e.kind.includes("AUDIT") && !e.kind.includes("ADMIN")) return false;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      return (
        e.title.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q) ||
        (e.agent_id && e.agent_id.toLowerCase().includes(q)) ||
        (e.team_id && e.team_id.toLowerCase().includes(q)) ||
        e.kind.toLowerCase().includes(q)
      );
    }
    return true;
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="page-title">Governance Events &amp; Circuit Breakers</h1>
          <p className="page-description">
            Immutable chronological audit log of velocity trips, threshold crossings, dynamic substitutions, and human overrides.
          </p>
        </div>

        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={loadEvents}
        >
          <RefreshCwIcon size={12} />
          <span>{loading ? "Refreshing..." : "Refresh Feed"}</span>
        </button>
      </div>

      {error && (
        <div className="notice-box danger">
          <AlertCircleIcon size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* 4-Metric Event Breakdown Strip */}
      <div className="stats-strip">
        <div className="stat-cell">
          <div className="stat-label">Total Logged Events</div>
          <div className="stat-value money">{events.length}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: "var(--danger)" }}>Runaway Breaker Trips</div>
          <div className="stat-value money" style={{ color: "var(--danger)" }}>{runawayEvents.length}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: "var(--warning)" }}>80% Warning Thresholds</div>
          <div className="stat-value money" style={{ color: "var(--warning)" }}>{warningEvents.length}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-label" style={{ color: "var(--brand-blue)" }}>100% Hard Cap Blocks</div>
          <div className="stat-value money" style={{ color: "var(--brand-blue)" }}>{blockEvents.length}</div>
        </div>
      </div>

      {/* Filter & Search Bar */}
      <div className="shadcn-card" style={{ padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        {/* Filter Pills */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            className={`btn btn-sm ${filterType === "ALL" ? "btn-primary" : "btn-outline"}`}
            onClick={() => setFilterType("ALL")}
          >
            All Events ({events.length})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${filterType === "RUNAWAY" ? "btn-danger" : "btn-outline"}`}
            onClick={() => setFilterType("RUNAWAY")}
          >
            Runaway Incidents ({runawayEvents.length})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${filterType === "WARNING" ? "btn-primary" : "btn-outline"}`}
            onClick={() => setFilterType("WARNING")}
          >
            80% Warnings ({warningEvents.length})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${filterType === "BLOCK" ? "btn-danger" : "btn-outline"}`}
            onClick={() => setFilterType("BLOCK")}
          >
            Hard Blocks ({blockEvents.length})
          </button>
          <button
            type="button"
            className={`btn btn-sm ${filterType === "AUDIT" ? "btn-primary" : "btn-outline"}`}
            onClick={() => setFilterType("AUDIT")}
          >
            Admin Audits ({auditEvents.length})
          </button>
        </div>

        {/* Search Input */}
        <div style={{ position: "relative", minWidth: 240 }}>
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)", pointerEvents: "none" }}>
            <SearchIcon size={13} />
          </span>
          <input
            type="text"
            className="form-input font-mono"
            placeholder="Search by agent, reason, ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{ paddingLeft: 30, fontSize: 12.5 }}
          />
        </div>
      </div>

      {/* Event Timeline Table */}
      <div className="shadcn-card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="table-container" style={{ border: "none" }}>
          {loading ? (
            <div className="empty-state">Loading governance telemetry feed...</div>
          ) : filteredEvents.length === 0 ? (
            <div className="empty-state">No governance events match the current filter.</div>
          ) : (
            <table className="shadcn-table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Severity</th>
                  <th>Target Scope</th>
                  <th>Event Title &amp; Description</th>
                  <th>Actor / Cause</th>
                  <th style={{ textAlign: "right" }}>Action</th>
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
                        <span
                          className={`badge ${
                            ev.severity === "danger" || isRunaway
                              ? "badge-danger"
                              : ev.severity === "warn"
                              ? "badge-warning"
                              : ev.severity === "info"
                              ? "badge-cyan"
                              : "badge-neutral"
                          }`}
                          style={{ fontSize: 10.5, textTransform: "uppercase" }}
                        >
                          {ev.severity || "INFO"}
                        </span>
                      </td>

                      <td>
                        {ev.agent_id ? (
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <CpuIcon size={12} className="text-secondary" />
                            <Link
                              href={`/agents/${ev.agent_id}`}
                              style={{ fontWeight: 600, color: "var(--brand-blue)", textDecoration: "none", fontSize: 12.5 }}
                            >
                              {ev.agent_id}
                            </Link>
                          </div>
                        ) : ev.team_id ? (
                          <span className="badge badge-neutral" style={{ fontSize: 11 }}>
                            Team: {ev.team_id}
                          </span>
                        ) : (
                          <span className="badge badge-neutral" style={{ fontSize: 11 }}>System</span>
                        )}
                      </td>

                      <td>
                        <div>
                          <strong style={{ color: "var(--text-primary)", fontSize: 13 }}>
                            {ev.title}
                          </strong>
                          <div style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 2 }}>
                            {ev.description}
                          </div>
                        </div>
                      </td>

                      <td>
                        <code style={{ fontSize: 11, backgroundColor: "var(--bg-muted)", padding: "2px 6px", borderRadius: 4 }}>
                          {ev.actor || ev.kind}
                        </code>
                      </td>

                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {isPaused && targetAgent ? (
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            style={{ backgroundColor: "var(--ok)", borderColor: "var(--ok)", fontSize: 11 }}
                            onClick={() => setResumingAgent(targetAgent)}
                          >
                            Resume
                          </button>
                        ) : ev.agent_id ? (
                          <Link
                            href={`/agents/${ev.agent_id}`}
                            className="btn btn-ghost btn-sm"
                          >
                            <span>Inspect</span>
                            <ArrowRightIcon size={11} />
                          </Link>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
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
    </div>
  );
}
