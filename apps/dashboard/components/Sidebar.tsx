"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ShieldIcon,
  LayoutDashboardIcon,
  TerminalIcon,
  UsersIcon,
  CpuIcon,
  ClockIcon,
  BookOpenIcon,
  ActivityIcon,
  SettingsIcon,
  SearchIcon,
} from "./Icons";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  badge?: string;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: "DASHBOARD",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboardIcon },
      { href: "/playground", label: "Playground", icon: TerminalIcon, badge: "Live" },
    ],
  },
  {
    title: "GOVERNANCE",
    items: [
      { href: "/teams", label: "Teams", icon: UsersIcon },
      { href: "/agents", label: "Agents", icon: CpuIcon },
      { href: "/sessions", label: "Sessions", icon: ClockIcon },
    ],
  },
  {
    title: "AUDIT & TELEMETRY",
    items: [
      { href: "/ledger", label: "Ledger", icon: BookOpenIcon },
      { href: "/events", label: "Events & Alerts", icon: ActivityIcon },
      { href: "/settings/providers", label: "Providers & Rates", icon: SettingsIcon },
    ],
  },
];

interface SidebarProps {
  onOpenSearch?: () => void;
}

export function Sidebar({ onOpenSearch }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside className="sidebar-container">
      {/* Brand Header */}
      <div
        style={{
          height: 60,
          padding: "0 20px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          borderBottom: "1px solid var(--border-app)",
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--primary)",
            color: "#ffffff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <ShieldIcon size={18} />
        </div>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: "-0.01em", color: "var(--text-primary)" }}>
            AgentGuard
          </div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: -2 }}>
            Budget Controller
          </div>
        </div>
      </div>

      {/* Quick Search Bar — triggers command palette */}
      <div style={{ padding: "14px 16px 6px" }}>
        <button
          type="button"
          onClick={onOpenSearch}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            backgroundColor: "var(--bg-app)",
            border: "1px solid var(--border-app)",
            borderRadius: "var(--radius-md)",
            color: "var(--text-muted)",
            fontSize: 12.5,
            width: "100%",
            cursor: "pointer",
            transition: "border-color 0.15s ease",
          }}
        >
          <SearchIcon size={14} />
          <span style={{ flex: 1, textAlign: "left" }}>Search scopes...</span>
          <kbd
            style={{
              fontSize: 10,
              fontFamily: "var(--font-mono)",
              backgroundColor: "#ffffff",
              padding: "1px 4px",
              borderRadius: "var(--radius-xs)",
              border: "1px solid var(--border-app)",
            }}
          >
            ⌘K
          </kbd>
        </button>
      </div>

      {/* Nav Navigation Groups */}
      <nav style={{ flex: 1, padding: "10px 12px", overflowY: "auto" }}>
        {NAV_GROUPS.map((group) => (
          <div key={group.title} style={{ marginBottom: 18 }}>
            <div
              style={{
                fontSize: 10.5,
                fontWeight: 600,
                color: "var(--text-muted)",
                letterSpacing: "0.05em",
                padding: "4px 10px 6px",
              }}
            >
              {group.title}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {group.items.map((item) => {
                const Icon = item.icon;
                const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "8px 12px",
                      borderRadius: "var(--radius-md)",
                      fontSize: 13,
                      fontWeight: isActive ? 600 : 500,
                      color: isActive ? "var(--primary)" : "var(--text-secondary)",
                      backgroundColor: isActive ? "var(--bg-muted)" : "transparent",
                      textDecoration: "none",
                      transition: "all 0.15s ease",
                    }}
                  >
                    <Icon size={16} className={isActive ? "text-primary" : "text-secondary"} />
                    <span style={{ flex: 1 }}>{item.label}</span>
                    {item.badge && (
                      <span
                        className="badge badge-cyan"
                        style={{ fontSize: 10, padding: "1px 5px" }}
                      >
                        {item.badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Operator Footer */}
      <div
        style={{
          padding: "14px 16px",
          borderTop: "1px solid var(--border-app)",
          display: "flex",
          alignItems: "center",
          gap: 10,
          backgroundColor: "var(--bg-sidebar)",
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: "9999px",
            backgroundColor: "var(--brand-blue-soft)",
            color: "var(--brand-blue)",
            border: "1px solid var(--brand-blue-border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: 12,
          }}
        >
          AG
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: "var(--text-primary)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            bootstrap-admin
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 5, marginTop: 1 }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "9999px",
                backgroundColor: "var(--ok)",
                display: "inline-block",
              }}
            />
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Memory Store</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
