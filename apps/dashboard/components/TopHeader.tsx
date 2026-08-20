"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { TerminalIcon, PlusIcon, ShieldIcon } from "./Icons";
import { NotificationBell } from "./NotificationBell";
import { UserMenu } from "./UserMenu";
import { useSession } from "@/lib/session-context";

const TITLE_MAP: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Overview", subtitle: "Real-time AI spend, atomic reservations & budget runway" },
  "/teams": { title: "Teams", subtitle: "Organizational budget allocations and spend aggregation" },
  "/agents": { title: "Agents", subtitle: "Autonomous agent budgets, routing rules & circuit breakers" },
  "/sessions": { title: "Sessions", subtitle: "Lifecycle-scoped agent sessions and hard caps" },
  "/playground": { title: "Playground", subtitle: "Interactive inference simulation and 10-stage execution trace" },
  "/ledger": { title: "Ledger", subtitle: "Append-only financial audit trail with nano-USD precision" },
  "/events": { title: "Events & Alerts", subtitle: "Circuit breaker events, 80% warnings and administrative logs" },
  "/settings/providers": { title: "Providers & Pricing", subtitle: "LLM provider connectivity, credentials and pricing catalog" },
};

export function TopHeader() {
  const pathname = usePathname();
  const session = useSession();
  const canWrite = session.role === "OPERATOR" || session.role === "ADMIN";
  const current = TITLE_MAP[pathname] || { title: "AgentGuard", subtitle: "Financial Authorization Firewall" };

  return (
    <header className="top-header-bar">
      {/* Page Title & Breadcrumb */}
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h1 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.01em" }}>
            {current.title}
          </h1>
          <span className="badge badge-ok" style={{ fontSize: 11 }}>
            <ShieldIcon size={12} /> Live Firewall Active
          </span>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 1 }}>
          {current.subtitle}
        </div>
      </div>

      {/* Header Actions */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Link
          href="/playground"
          className="btn btn-outline btn-sm"
          style={{ gap: 6 }}
        >
          <TerminalIcon size={14} />
          <span>Test Inference</span>
        </Link>

        {canWrite && (
          <Link
            href="/agents"
            className="btn btn-primary btn-sm"
            style={{ gap: 6 }}
          >
            <PlusIcon size={14} />
            <span>New Agent</span>
          </Link>
        )}

        <NotificationBell />
        <UserMenu />
      </div>
    </header>
  );
}
