"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { SearchIcon, XIcon, UsersIcon, CpuIcon, ClockIcon } from "./Icons";
import type { TeamSummary, AgentSummary, SessionView } from "../lib/types";

interface SearchResult {
  type: "team" | "agent" | "session" | "page";
  id: string;
  label: string;
  sublabel: string;
  href: string;
}

const NAV_PAGES: SearchResult[] = [
  { type: "page", id: "overview", label: "Overview", sublabel: "Dashboard home", href: "/" },
  { type: "page", id: "playground", label: "Playground", sublabel: "Interactive API testing", href: "/playground" },
  { type: "page", id: "teams", label: "Teams", sublabel: "Team budget governance", href: "/teams" },
  { type: "page", id: "agents", label: "Agents", sublabel: "Agent fleet management", href: "/agents" },
  { type: "page", id: "sessions", label: "Sessions", sublabel: "Session lifecycle tracking", href: "/sessions" },
  { type: "page", id: "ledger", label: "Ledger", sublabel: "Financial transaction log", href: "/ledger" },
  { type: "page", id: "events", label: "Events & Alerts", sublabel: "Audit & telemetry feed", href: "/events" },
  { type: "page", id: "providers", label: "Providers & Rates", sublabel: "Model provider settings", href: "/settings/providers" },
];

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  // ⌘K / Ctrl+K to open
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        onOpenChange(!open);
      }
      if (e.key === "Escape" && open) {
        onOpenChange(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setQuery("");
      setResults(NAV_PAGES);
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // Search across API data
  const doSearch = useCallback(
    async (q: string) => {
      const lower = q.toLowerCase().trim();
      if (!lower) {
        setResults(NAV_PAGES);
        setActiveIdx(0);
        return;
      }

      // Filter pages first
      const pageResults = NAV_PAGES.filter(
        (p) => p.label.toLowerCase().includes(lower) || p.sublabel.toLowerCase().includes(lower)
      );

      setLoading(true);

      try {
        // Fetch teams, agents, sessions concurrently
        const [teamsRes, agentsRes, sessionsRes] = await Promise.allSettled([
          fetch("/api/teams").then((r) => (r.ok ? r.json() : [])),
          fetch("/api/agents").then((r) => (r.ok ? r.json() : [])),
          fetch("/api/sessions").then((r) => (r.ok ? r.json() : [])),
        ]);

        const teams: TeamSummary[] =
          teamsRes.status === "fulfilled" ? (Array.isArray(teamsRes.value) ? teamsRes.value : []) : [];
        const agents: AgentSummary[] =
          agentsRes.status === "fulfilled" ? (Array.isArray(agentsRes.value) ? agentsRes.value : []) : [];
        const sessions: SessionView[] =
          sessionsRes.status === "fulfilled" ? (Array.isArray(sessionsRes.value) ? sessionsRes.value : []) : [];

        const teamResults: SearchResult[] = teams
          .filter((t) => t.team_id.toLowerCase().includes(lower))
          .slice(0, 5)
          .map((t) => ({
            type: "team" as const,
            id: t.team_id,
            label: t.team_id,
            sublabel: `Team · ${t.agent_count} agents · ${t.utilization_percent.toFixed(0)}% utilized`,
            href: `/teams/${t.team_id}`,
          }));

        const agentResults: SearchResult[] = agents
          .filter(
            (a) =>
              a.agent_id.toLowerCase().includes(lower) || a.team_id.toLowerCase().includes(lower)
          )
          .slice(0, 5)
          .map((a) => ({
            type: "agent" as const,
            id: a.agent_id,
            label: a.agent_id,
            sublabel: `Agent · ${a.team_id} · ${a.status}`,
            href: `/agents/${a.agent_id}`,
          }));

        const sessionResults: SearchResult[] = sessions
          .filter(
            (s) =>
              s.session_id.toLowerCase().includes(lower) ||
              s.agent_id.toLowerCase().includes(lower)
          )
          .slice(0, 5)
          .map((s) => ({
            type: "session" as const,
            id: s.session_id,
            label: s.session_id.length > 28 ? s.session_id.slice(0, 28) + "…" : s.session_id,
            sublabel: `Session · ${s.agent_id} · ${s.status}`,
            href: `/sessions`,
          }));

        const combined = [...pageResults, ...teamResults, ...agentResults, ...sessionResults];
        setResults(combined);
        setActiveIdx(0);
      } catch {
        // If API fails, just show page results
        setResults(pageResults);
        setActiveIdx(0);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Debounced search
  useEffect(() => {
    const timeout = setTimeout(() => doSearch(query), 200);
    return () => clearTimeout(timeout);
  }, [query, doSearch]);

  function navigate(result: SearchResult) {
    onOpenChange(false);
    router.push(result.href);
  }

  function onKeyNav(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && results[activeIdx]) {
      e.preventDefault();
      navigate(results[activeIdx]);
    }
  }

  function getIcon(type: string) {
    switch (type) {
      case "team":
        return <UsersIcon size={14} />;
      case "agent":
        return <CpuIcon size={14} />;
      case "session":
        return <ClockIcon size={14} />;
      default:
        return <SearchIcon size={14} />;
    }
  }

  function getBadgeClass(type: string) {
    switch (type) {
      case "team":
        return "badge-indigo";
      case "agent":
        return "badge-cyan";
      case "session":
        return "badge-warning";
      default:
        return "badge-neutral";
    }
  }

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onOpenChange(false);
      }}
      style={{ alignItems: "flex-start", paddingTop: 100 }}
    >
      <div
        className="modal-content"
        style={{ padding: 0, maxWidth: 540, width: "100%", overflow: "hidden" }}
      >
        {/* Search input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "12px 16px",
            borderBottom: "1px solid var(--border-app)",
          }}
        >
          <SearchIcon size={16} className="text-muted" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyNav}
            placeholder="Search teams, agents, sessions, pages..."
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              background: "transparent",
              fontSize: 14,
              color: "var(--text-primary)",
            }}
          />
          {loading && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Searching…</span>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onOpenChange(false)}
            style={{ padding: 4 }}
          >
            <XIcon size={14} />
          </button>
        </div>

        {/* Results */}
        <div style={{ maxHeight: 360, overflowY: "auto" }}>
          {results.length === 0 ? (
            <div
              style={{
                padding: "32px 16px",
                textAlign: "center",
                fontSize: 13,
                color: "var(--text-muted)",
              }}
            >
              No results found for &ldquo;{query}&rdquo;
            </div>
          ) : (
            <div style={{ padding: "6px 0" }}>
              {results.map((r, idx) => (
                <button
                  key={`${r.type}-${r.id}`}
                  type="button"
                  onClick={() => navigate(r)}
                  onMouseEnter={() => setActiveIdx(idx)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    width: "100%",
                    padding: "8px 16px",
                    border: "none",
                    background: idx === activeIdx ? "var(--bg-muted)" : "transparent",
                    cursor: "pointer",
                    textAlign: "left",
                    transition: "background 0.1s",
                  }}
                >
                  <span
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: "var(--radius-md)",
                      backgroundColor: "var(--bg-app)",
                      border: "1px solid var(--border-app)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "var(--text-muted)",
                      flexShrink: 0,
                    }}
                  >
                    {getIcon(r.type)}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: "var(--text-primary)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {r.label}
                    </div>
                    <div
                      style={{
                        fontSize: 11.5,
                        color: "var(--text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {r.sublabel}
                    </div>
                  </div>
                  <span
                    className={`badge ${getBadgeClass(r.type)}`}
                    style={{ fontSize: 10, padding: "1px 6px", flexShrink: 0 }}
                  >
                    {r.type}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer hints */}
        <div
          style={{
            padding: "8px 16px",
            borderTop: "1px solid var(--border-app)",
            display: "flex",
            gap: 16,
            fontSize: 11,
            color: "var(--text-muted)",
          }}
        >
          <span>
            <kbd style={{ fontSize: 10, fontFamily: "var(--font-mono)", backgroundColor: "var(--bg-app)", padding: "1px 4px", borderRadius: "var(--radius-xs)", border: "1px solid var(--border-app)" }}>↑↓</kbd>{" "}
            Navigate
          </span>
          <span>
            <kbd style={{ fontSize: 10, fontFamily: "var(--font-mono)", backgroundColor: "var(--bg-app)", padding: "1px 4px", borderRadius: "var(--radius-xs)", border: "1px solid var(--border-app)" }}>↵</kbd>{" "}
            Open
          </span>
          <span>
            <kbd style={{ fontSize: 10, fontFamily: "var(--font-mono)", backgroundColor: "var(--bg-app)", padding: "1px 4px", borderRadius: "var(--radius-xs)", border: "1px solid var(--border-app)" }}>Esc</kbd>{" "}
            Close
          </span>
        </div>
      </div>
    </div>
  );
}
