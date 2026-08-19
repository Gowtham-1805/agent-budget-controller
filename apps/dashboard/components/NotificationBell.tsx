"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import {
  BellIcon,
  AlertCircleIcon,
  ActivityIcon,
  ArrowRightIcon,
  CheckIcon,
  RefreshCwIcon,
} from "./Icons";
import type { EventItem } from "../lib/types";

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<"ALL" | "DANGER" | "WARN">("ALL");
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const ref = useRef<HTMLDivElement>(null);

  // Initialize read IDs from localStorage if available
  useEffect(() => {
    try {
      const stored = localStorage.getItem("abc_read_notifications");
      if (stored) {
        setReadIds(new Set(JSON.parse(stored)));
      }
    } catch {}
  }, []);

  function saveReadIds(newSet: Set<string>) {
    setReadIds(newSet);
    try {
      localStorage.setItem("abc_read_notifications", JSON.stringify(Array.from(newSet)));
    } catch {}
  }

  function loadEvents() {
    setLoading(true);
    fetch("/api/events?limit=25")
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        const items = Array.isArray(data) ? data : [];
        setEvents(items);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  // Initial fetch and auto-polling every 20s
  useEffect(() => {
    loadEvents();
    const interval = setInterval(loadEvents, 20000);
    return () => clearInterval(interval);
  }, []);

  // Click outside listener
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handler);
    }
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Count unread
  const unreadEvents = events.filter((e) => !readIds.has(e.event_id));
  const unreadCount = unreadEvents.length;

  const filteredEvents = events.filter((e) => {
    if (filter === "DANGER") return e.severity === "danger";
    if (filter === "WARN") return e.severity === "warn" || e.severity === "warning";
    return true;
  });

  function markAllAsRead() {
    const all = new Set(events.map((e) => e.event_id));
    saveReadIds(all);
  }

  function markSingleAsRead(id: string) {
    const next = new Set(readIds);
    next.add(id);
    saveReadIds(next);
  }

  function timeAgo(ts: string) {
    const diff = Math.max(0, Date.now() - new Date(ts).getTime());
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      {/* Sleek Trigger Button matching shadcn rounded card pill */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Notifications"
        style={{
          width: 38,
          height: 38,
          borderRadius: "10px",
          border: open ? "1.5px solid var(--primary)" : "1px solid var(--border-app)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: open ? "var(--text-primary)" : "var(--text-secondary)",
          cursor: "pointer",
          backgroundColor: open ? "var(--bg-muted)" : "#ffffff",
          position: "relative",
          transition: "all 0.15s ease",
          boxShadow: open
            ? "0 0 0 3px rgba(15, 23, 42, 0.08)"
            : "0 1px 2px rgba(0, 0, 0, 0.04)",
        }}
        onMouseEnter={(e) => {
          if (!open) {
            e.currentTarget.style.backgroundColor = "var(--bg-app)";
            e.currentTarget.style.borderColor = "var(--border-muted)";
            e.currentTarget.style.color = "var(--text-primary)";
          }
        }}
        onMouseLeave={(e) => {
          if (!open) {
            e.currentTarget.style.backgroundColor = "#ffffff";
            e.currentTarget.style.borderColor = "var(--border-app)";
            e.currentTarget.style.color = "var(--text-secondary)";
          }
        }}
        title="Notifications & Governance Alerts"
      >
        <BellIcon size={18} />

        {/* Floating Pulsing Badge */}
        {unreadCount > 0 && (
          <span
            style={{
              position: "absolute",
              top: -3,
              right: -3,
              minWidth: 18,
              height: 18,
              borderRadius: "9999px",
              backgroundColor: "var(--danger)",
              color: "#ffffff",
              fontSize: 10,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "0 4px",
              border: "2px solid #ffffff",
              boxShadow: "0 2px 4px rgba(220, 38, 38, 0.3)",
              animation: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
            }}
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {/* Floating Popover Card */}
      {open && (
        <div
          className="shadcn-card"
          style={{
            position: "absolute",
            top: "calc(100% + 10px)",
            right: 0,
            width: 400,
            maxHeight: 520,
            padding: 0,
            borderRadius: "14px",
            boxShadow:
              "0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1), 0 0 0 1px rgba(0, 0, 0, 0.05)",
            zIndex: 1000,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            backgroundColor: "#ffffff",
          }}
        >
          {/* Header Bar */}
          <div
            style={{
              padding: "14px 18px",
              borderBottom: "1px solid var(--border-app)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              backgroundColor: "#ffffff",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h3
                style={{
                  fontSize: 15,
                  fontWeight: 700,
                  color: "var(--text-primary)",
                  margin: 0,
                }}
              >
                Notifications
              </h3>
              {unreadCount > 0 ? (
                <span className="badge badge-danger" style={{ fontSize: 10, padding: "2px 6px" }}>
                  {unreadCount} unread
                </span>
              ) : (
                <span className="badge badge-neutral" style={{ fontSize: 10, padding: "2px 6px" }}>
                  {events.length} total
                </span>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {unreadCount > 0 && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={markAllAsRead}
                  style={{
                    fontSize: 11.5,
                    padding: "4px 8px",
                    height: "auto",
                    color: "var(--brand-blue)",
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                  }}
                  title="Mark all as read"
                >
                  <CheckIcon size={12} />
                  <span>Mark all read</span>
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-sm icon-only"
                onClick={loadEvents}
                disabled={loading}
                style={{ width: 28, height: 28, padding: 0 }}
                title="Refresh notifications"
              >
                <RefreshCwIcon
                  size={13}
                  className={loading ? "animate-spin" : ""}
                />
              </button>
            </div>
          </div>

          {/* Filter Pills Bar */}
          <div
            style={{
              padding: "8px 14px",
              backgroundColor: "var(--bg-app)",
              borderBottom: "1px solid var(--border-app)",
              display: "flex",
              gap: 6,
            }}
          >
            <button
              type="button"
              onClick={() => setFilter("ALL")}
              style={{
                border: "none",
                background: filter === "ALL" ? "#ffffff" : "transparent",
                color: filter === "ALL" ? "var(--text-primary)" : "var(--text-muted)",
                fontSize: 11.5,
                fontWeight: filter === "ALL" ? 600 : 500,
                padding: "4px 10px",
                borderRadius: "6px",
                cursor: "pointer",
                boxShadow: filter === "ALL" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
                transition: "all 0.15s ease",
              }}
            >
              All ({events.length})
            </button>
            <button
              type="button"
              onClick={() => setFilter("DANGER")}
              style={{
                border: "none",
                background: filter === "DANGER" ? "#ffffff" : "transparent",
                color: filter === "DANGER" ? "var(--danger)" : "var(--text-muted)",
                fontSize: 11.5,
                fontWeight: filter === "DANGER" ? 600 : 500,
                padding: "4px 10px",
                borderRadius: "6px",
                cursor: "pointer",
                boxShadow: filter === "DANGER" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
                transition: "all 0.15s ease",
              }}
            >
              Critical Breakers ({events.filter((e) => e.severity === "danger").length})
            </button>
            <button
              type="button"
              onClick={() => setFilter("WARN")}
              style={{
                border: "none",
                background: filter === "WARN" ? "#ffffff" : "transparent",
                color: filter === "WARN" ? "var(--warning)" : "var(--text-muted)",
                fontSize: 11.5,
                fontWeight: filter === "WARN" ? 600 : 500,
                padding: "4px 10px",
                borderRadius: "6px",
                cursor: "pointer",
                boxShadow: filter === "WARN" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
                transition: "all 0.15s ease",
              }}
            >
              Warnings ({events.filter((e) => e.severity === "warn" || e.severity === "warning").length})
            </button>
          </div>

          {/* List Area */}
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              maxHeight: 320,
              display: "flex",
              flexDirection: "column",
            }}
          >
            {filteredEvents.length === 0 ? (
              <div
                style={{
                  padding: "48px 24px",
                  textAlign: "center",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 10,
                  color: "var(--text-muted)",
                }}
              >
                <div
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: "9999px",
                    backgroundColor: "var(--bg-muted)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "var(--text-muted)",
                  }}
                >
                  <BellIcon size={22} />
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  No notifications in this filter
                </div>
                <div style={{ fontSize: 12, maxWidth: 260 }}>
                  Circuit breaker triggers, 80% budget warnings, and agent interventions will appear here.
                </div>
              </div>
            ) : (
              <div>
                {filteredEvents.map((ev) => {
                  const isRead = readIds.has(ev.event_id);
                  const isDanger = ev.severity === "danger";
                  const isWarn = ev.severity === "warn" || ev.severity === "warning";

                  return (
                    <div
                      key={ev.event_id}
                      onClick={() => markSingleAsRead(ev.event_id)}
                      style={{
                        padding: "12px 18px",
                        display: "flex",
                        gap: 12,
                        alignItems: "flex-start",
                        borderBottom: "1px solid var(--border-app)",
                        backgroundColor: isRead ? "transparent" : "var(--brand-blue-soft)",
                        cursor: "pointer",
                        transition: "background-color 0.15s ease",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.backgroundColor = isRead
                          ? "var(--bg-app)"
                          : "rgba(239, 246, 255, 0.9)";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = isRead
                          ? "transparent"
                          : "var(--brand-blue-soft)";
                      }}
                    >
                      {/* Left Visual Icon / Status Indicator */}
                      <div
                        style={{
                          width: 32,
                          height: 32,
                          borderRadius: "8px",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          flexShrink: 0,
                          backgroundColor: isDanger
                            ? "var(--danger-soft)"
                            : isWarn
                            ? "var(--warning-soft)"
                            : "var(--ok-soft)",
                          color: isDanger
                            ? "var(--danger)"
                            : isWarn
                            ? "var(--warning)"
                            : "var(--ok)",
                          border: `1px solid ${
                            isDanger
                              ? "var(--danger-border)"
                              : isWarn
                              ? "var(--warning-border)"
                              : "var(--ok-border)"
                          }`,
                        }}
                      >
                        {isDanger ? (
                          <AlertCircleIcon size={16} />
                        ) : isWarn ? (
                          <ActivityIcon size={16} />
                        ) : (
                          <CheckIcon size={16} />
                        )}
                      </div>

                      {/* Middle Content */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: 8,
                            marginBottom: 2,
                          }}
                        >
                          <div
                            style={{
                              fontSize: 13,
                              fontWeight: isRead ? 600 : 700,
                              color: "var(--text-primary)",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {ev.title}
                          </div>
                          <span
                            style={{
                              fontSize: 11,
                              color: "var(--text-muted)",
                              flexShrink: 0,
                              fontFamily: "var(--font-mono)",
                            }}
                          >
                            {timeAgo(ev.occurred_at)}
                          </span>
                        </div>

                        <div
                          style={{
                            fontSize: 12,
                            color: "var(--text-secondary)",
                            lineHeight: 1.4,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            marginBottom: 4,
                          }}
                        >
                          {ev.description || "Circuit breaker threshold condition logged."}
                        </div>

                        {/* Metadata Tag Footer */}
                        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                          {ev.agent_id && (
                            <Link
                              href={`/agents/${ev.agent_id}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setOpen(false);
                              }}
                              className="badge badge-neutral font-mono"
                              style={{
                                fontSize: 10.5,
                                textDecoration: "none",
                                padding: "1px 6px",
                              }}
                            >
                              agent: {ev.agent_id}
                            </Link>
                          )}
                          {ev.team_id && (
                            <Link
                              href={`/teams/${ev.team_id}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setOpen(false);
                              }}
                              className="badge badge-neutral font-mono"
                              style={{
                                fontSize: 10.5,
                                textDecoration: "none",
                                padding: "1px 6px",
                              }}
                            >
                              team: {ev.team_id}
                            </Link>
                          )}
                          {ev.amount_usd && (
                            <span className="badge badge-neutral font-mono" style={{ fontSize: 10.5, padding: "1px 6px" }}>
                              ${ev.amount_usd}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Unread Dot Indicator */}
                      {!isRead && (
                        <span
                          style={{
                            width: 6,
                            height: 6,
                            borderRadius: "9999px",
                            backgroundColor: "var(--brand-blue)",
                            flexShrink: 0,
                            marginTop: 6,
                          }}
                          title="Unread"
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Footer View All Bar */}
          <div
            style={{
              padding: "12px 18px",
              borderTop: "1px solid var(--border-app)",
              backgroundColor: "var(--bg-app)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
              Real-time audit log
            </span>
            <Link
              href="/events"
              className="btn btn-outline btn-sm"
              onClick={() => setOpen(false)}
              style={{
                fontSize: 12,
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                height: 28,
              }}
            >
              <span>View All Events</span>
              <ArrowRightIcon size={12} />
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
