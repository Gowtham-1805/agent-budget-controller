"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { EyeIcon, EyeOffIcon, ShieldIcon } from "./Icons";
import { safeNext } from "@/lib/redirect";

export function LoginForm({ next }: { next: string | null }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [status, setStatus] = useState<"idle" | "submitting">("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "submitting") return; // guards against a double submit

    setStatus("submitting");
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        // Render the server's message verbatim -- never substitute a more
        // specific one client-side, or the frontend would reintroduce the
        // account-enumeration the backend was built to avoid.
        setError(
          body?.error?.message ?? "Unable to sign in. Please try again.",
        );
        setStatus("idle");
        return;
      }

      router.push(safeNext(next));
      router.refresh();
    } catch {
      setError("Network error. Please check your connection and try again.");
      setStatus("idle");
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          marginBottom: 24,
        }}
      >
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--primary)",
            color: "#ffffff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            marginBottom: 12,
          }}
        >
          <ShieldIcon size={24} />
        </div>
        <h1
          style={{
            fontSize: 18,
            fontWeight: 700,
            color: "var(--text-primary)",
            letterSpacing: "-0.01em",
          }}
        >
          Sign in to AgentGuard
        </h1>
        <p
          style={{
            fontSize: 12.5,
            color: "var(--text-muted)",
            marginTop: 4,
          }}
        >
          Financial authorization firewall for autonomous AI agents
        </p>
      </div>

      {error && (
        <div
          role="alert"
          aria-live="assertive"
          className="notice-box danger"
          style={{ marginBottom: 16 }}
        >
          {error}
        </div>
      )}

      <div style={{ marginBottom: 14 }}>
        <label
          htmlFor="email"
          style={{
            display: "block",
            fontSize: 12.5,
            fontWeight: 500,
            color: "var(--text-secondary)",
            marginBottom: 5,
          }}
        >
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          autoFocus
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="form-input"
          aria-invalid={error ? "true" : "false"}
        />
      </div>

      <div style={{ marginBottom: 20 }}>
        <label
          htmlFor="password"
          style={{
            display: "block",
            fontSize: 12.5,
            fontWeight: 500,
            color: "var(--text-secondary)",
            marginBottom: 5,
          }}
        >
          Password
        </label>
        <div style={{ position: "relative" }}>
          <input
            id="password"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="form-input"
            style={{ paddingRight: 40 }}
            aria-invalid={error ? "true" : "false"}
          />
          <button
            type="button"
            onClick={() => setShowPassword((v) => !v)}
            aria-label={showPassword ? "Hide password" : "Show password"}
            aria-pressed={showPassword}
            style={{
              position: "absolute",
              right: 8,
              top: "50%",
              transform: "translateY(-50%)",
              background: "none",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
              padding: 4,
            }}
          >
            {showPassword ? <EyeOffIcon size={16} /> : <EyeIcon size={16} />}
          </button>
        </div>
      </div>

      <button
        type="submit"
        className="btn btn-primary"
        disabled={status === "submitting"}
        aria-busy={status === "submitting"}
        style={{ width: "100%", padding: "9px 14px", fontSize: 13.5 }}
      >
        {status === "submitting" ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
