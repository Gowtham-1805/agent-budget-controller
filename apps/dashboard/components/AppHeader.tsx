"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ActivityIcon,
  CpuIcon,
  DatabaseIcon,
  LayersIcon,
  PlayIcon,
  SettingsIcon,
  ShieldIcon,
  UsersIcon,
} from "./Icons";

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: ActivityIcon },
  { href: "/teams", label: "Teams", icon: UsersIcon },
  { href: "/agents", label: "Agents", icon: CpuIcon },
  { href: "/sessions", label: "Sessions", icon: LayersIcon },
  { href: "/playground", label: "Playground", icon: PlayIcon },
  { href: "/ledger", label: "Ledger", icon: DatabaseIcon },
  { href: "/events", label: "Events & Alerts", icon: ActivityIcon },
  { href: "/settings/providers", label: "Providers", icon: SettingsIcon },
];

export function AppHeader() {
  const pathname = usePathname();

  return (
    <header style={{ marginBottom: 20 }}>
      {/* Top Brand Bar */}
      <div className="app-header">
        <div className="brand-section">
          <Link href="/" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 28,
                height: 28,
                borderRadius: "var(--radius-sm)",
                background: "var(--primary-subtle)",
                border: "1px solid var(--primary-border)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--primary-text)",
              }}
            >
              <ShieldIcon size={16} />
            </div>
            <div>
              <div className="brand-title">AgentGuard</div>
              <div className="brand-subtitle">AI Spend Firewall &amp; Financial Governance</div>
            </div>
          </Link>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div className="live-indicator">
            <span className="pulse-dot" />
            <span>Pre-Inference Firewall Active</span>
          </div>

          <Link href="/playground" className="btn btn-primary btn-sm">
            <PlayIcon size={12} />
            <span>Test Inference</span>
          </Link>
        </div>
      </div>

      {/* Navigation Tabs */}
      <nav className="app-nav">
        {NAV_ITEMS.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          const IconComponent = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-link ${isActive ? "active" : ""}`}
            >
              <IconComponent size={14} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
