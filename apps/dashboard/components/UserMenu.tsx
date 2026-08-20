"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/lib/session-context";
import { LogOutIcon } from "./Icons";

const ROLE_BADGE_CLASS: Record<string, string> = {
  ADMIN: "badge-ok",
  OPERATOR: "badge-cyan",
  VIEWER: "badge",
};

export function UserMenu() {
  const session = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function onClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [open]);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  const initial = session.email.charAt(0).toUpperCase();

  return (
    <div ref={menuRef} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
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
          fontSize: 13,
          cursor: "pointer",
        }}
      >
        {initial}
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 8px)",
            width: 240,
            backgroundColor: "var(--bg-card)",
            border: "1px solid var(--border-card)",
            borderRadius: "var(--radius-md)",
            boxShadow: "var(--shadow-md)",
            padding: 8,
            zIndex: 50,
          }}
        >
          <div style={{ padding: "6px 10px 10px" }}>
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
              {session.email}
            </div>
            <span
              className={`badge ${ROLE_BADGE_CLASS[session.role] ?? ""}`}
              style={{ marginTop: 6, fontSize: 10.5 }}
            >
              {session.role}
            </span>
          </div>
          <div style={{ borderTop: "1px solid var(--border-app)", margin: "4px 0" }} />
          <button
            type="button"
            role="menuitem"
            onClick={handleSignOut}
            disabled={signingOut}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              width: "100%",
              padding: "8px 10px",
              background: "none",
              border: "none",
              borderRadius: "var(--radius-sm)",
              color: "var(--danger)",
              fontSize: 12.5,
              fontWeight: 500,
              cursor: signingOut ? "default" : "pointer",
              textAlign: "left",
            }}
          >
            <LogOutIcon size={14} />
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      )}
    </div>
  );
}
