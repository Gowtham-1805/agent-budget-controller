"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircleIcon,
  EyeIcon,
  EyeOffIcon,
  LockIcon,
} from "./Icons";
import { safeNext } from "@/lib/redirect";

/** Permissive on purpose. The gateway is the authority on what a valid
 *  address is; this only catches the obvious typo before a pointless round
 *  trip, and must never reject an address the server would have accepted. */
const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type FieldErrors = { email?: string; password?: string };

export function LoginForm({ next }: { next: string | null }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [status, setStatus] = useState<"idle" | "submitting">("idle");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [capsLock, setCapsLock] = useState(false);

  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  const submitting = status === "submitting";

  // Focus has to happen *after* the re-render that re-enables the field:
  // calling focus() straight from the submit handler is a silent no-op,
  // because at that point the input is still rendered disabled.
  useEffect(() => {
    if (error && status === "idle") {
      passwordRef.current?.focus();
      passwordRef.current?.select();
    }
  }, [error, status]);

  function validate(): FieldErrors {
    const next: FieldErrors = {};
    if (!email.trim()) next.email = "Enter your email address.";
    else if (!LOOKS_LIKE_EMAIL.test(email.trim()))
      next.email = "That does not look like an email address.";
    if (!password) next.password = "Enter your password.";
    return next;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return; // guards against a double submit

    // Presence/format only. Deliberately says nothing about whether the
    // account exists -- see the note on the server error below.
    const invalid = validate();
    setFieldErrors(invalid);
    if (invalid.email || invalid.password) {
      setError(null);
      (invalid.email ? emailRef : passwordRef).current?.focus();
      return;
    }

    setStatus("submitting");
    setError(null);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        // Render the server's message verbatim -- never substitute a more
        // specific one client-side, or the frontend would reintroduce the
        // account-enumeration the backend was built to avoid.
        setError(
          body?.error?.message ?? "Unable to sign in. Please try again.",
        );
        // The effect above puts the cursor where the retry starts, without
        // wiping what they typed -- a wrong password is usually a typo, not
        // a wrong account.
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

  function trackCapsLock(event: React.KeyboardEvent<HTMLInputElement>) {
    setCapsLock(event.getModifierState?.("CapsLock") ?? false);
  }

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      // method="post" matters even though handleSubmit always calls
      // preventDefault: if the page ever fails to hydrate, the browser falls
      // back to a native submit, and the default method (GET) would put the
      // password in the URL, browser history, and any Referer header.
      method="post"
    >
      {error && (
        <div role="alert" aria-live="assertive" className="auth-alert">
          <AlertCircleIcon size={15} className="auth-alert-icon" />
          <span>{error}</span>
        </div>
      )}

      <div className="auth-field">
        <div className="auth-label-row">
          <label htmlFor="email" className="auth-label">
            Email
          </label>
        </div>
        <input
          id="email"
          name="email"
          type="email"
          inputMode="email"
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          autoFocus
          required
          disabled={submitting}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (fieldErrors.email)
              setFieldErrors((f) => ({ ...f, email: undefined }));
          }}
          className="auth-input"
          placeholder="you@company.com"
          aria-invalid={fieldErrors.email ? "true" : "false"}
          aria-describedby={fieldErrors.email ? "email-error" : undefined}
          ref={emailRef}
        />
        {fieldErrors.email && (
          <div id="email-error" className="auth-field-error">
            <AlertCircleIcon size={12} />
            <span>{fieldErrors.email}</span>
          </div>
        )}
      </div>

      <div className="auth-field">
        <div className="auth-label-row">
          <label htmlFor="password" className="auth-label">
            Password
          </label>
        </div>
        <div className="auth-input-wrap">
          <input
            id="password"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            required
            disabled={submitting}
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              if (fieldErrors.password)
                setFieldErrors((f) => ({ ...f, password: undefined }));
            }}
            onKeyUp={trackCapsLock}
            onKeyDown={trackCapsLock}
            onBlur={() => setCapsLock(false)}
            className="auth-input auth-input-password"
            placeholder="••••••••••••"
            aria-invalid={fieldErrors.password ? "true" : "false"}
            aria-describedby={
              [
                fieldErrors.password ? "password-error" : null,
                capsLock ? "caps-hint" : null,
              ]
                .filter(Boolean)
                .join(" ") || undefined
            }
            ref={passwordRef}
          />
          <button
            type="button"
            onClick={() => {
              setShowPassword((v) => !v);
              passwordRef.current?.focus();
            }}
            aria-label={showPassword ? "Hide password" : "Show password"}
            aria-pressed={showPassword}
            aria-controls="password"
            disabled={submitting}
            className="auth-reveal"
          >
            {showPassword ? <EyeOffIcon size={16} /> : <EyeIcon size={16} />}
          </button>
        </div>
        {fieldErrors.password && (
          <div id="password-error" className="auth-field-error">
            <AlertCircleIcon size={12} />
            <span>{fieldErrors.password}</span>
          </div>
        )}
        {capsLock && !fieldErrors.password && (
          <div id="caps-hint" className="auth-hint" aria-live="polite">
            <AlertCircleIcon size={12} />
            <span>Caps Lock is on.</span>
          </div>
        )}
      </div>

      <button
        type="submit"
        className="auth-submit"
        disabled={submitting}
        aria-busy={submitting}
      >
        {submitting ? (
          <>
            <span className="auth-spinner" aria-hidden="true" />
            <span>Signing in…</span>
          </>
        ) : (
          "Sign in"
        )}
      </button>

      <div className="auth-footer">
        <LockIcon size={12} />
        <span>
          Sessions are bound to this browser and expire after 12 hours.
        </span>
      </div>
    </form>
  );
}
