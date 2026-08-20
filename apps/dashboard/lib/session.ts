/**
 * Server-only session lookup.
 *
 * This is the *authoritative* auth check -- called once per protected-page
 * render from `app/(app)/layout.tsx`. `middleware.ts` only checks whether a
 * cookie is present at all; it deliberately does not validate it (a network
 * round trip on the edge runtime, on every navigation, would be both slow
 * and a new failure mode). Validating here instead means the expensive check
 * happens once per render rather than once per asset, and a forged-but-present
 * cookie is still rejected, just one hop later than the edge.
 */
import { cookies } from "next/headers";
import type { SessionIdentity } from "./types";

const GATEWAY_URL = (
  process.env.GATEWAY_URL ?? "http://localhost:8080"
).replace(/\/$/, "");
const SESSION_COOKIE = "abc_dash_session";

export async function getSessionToken(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value ?? null;
}

export async function getSession(): Promise<SessionIdentity | null> {
  const token = await getSessionToken();
  if (!token) return null;

  try {
    const response = await fetch(`${GATEWAY_URL}/v1/auth/session`, {
      headers: { "X-ABC-Session": token },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as SessionIdentity;
  } catch {
    // The gateway being unreachable must fail closed, not grant access.
    return null;
  }
}
