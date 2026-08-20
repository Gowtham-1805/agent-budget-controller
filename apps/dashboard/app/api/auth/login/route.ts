import { NextResponse } from "next/server";

const GATEWAY_URL = (
  process.env.GATEWAY_URL ?? "http://localhost:8080"
).replace(/\/$/, "");

const SESSION_COOKIE = "abc_dash_session";
const CSRF_COOKIE = "abc_dash_csrf";

export const dynamic = "force-dynamic";

/**
 * Logs in against the gateway and re-sets its cookies as our own.
 *
 * The gateway's own Set-Cookie header (from `/v1/auth/login`) applies to the
 * gateway's origin, not the dashboard's -- the browser never talks to the
 * gateway directly, so that header never reaches it. `LoginResponse` carries
 * the raw session and CSRF tokens in the JSON body specifically so this
 * handler can set them as cookies scoped to *this* origin instead.
 */
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  if (!body) {
    return NextResponse.json(
      { error: { type: "invalid_request", message: "invalid request body" } },
      { status: 400 },
    );
  }

  const gatewayResponse = await fetch(`${GATEWAY_URL}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  const payload = await gatewayResponse.json().catch(() => ({}));
  if (!gatewayResponse.ok) {
    return NextResponse.json(payload, { status: gatewayResponse.status });
  }

  const secure = process.env.NODE_ENV === "production";
  const expiresAt = new Date(payload.expires_at);
  const maxAge = Math.max(
    60,
    Math.floor((expiresAt.getTime() - Date.now()) / 1000),
  );

  const response = NextResponse.json(
    {
      user_id: payload.user_id,
      email: payload.email,
      role: payload.role,
      tenant_id: payload.tenant_id,
      expires_at: payload.expires_at,
    },
    { status: 200 },
  );
  response.cookies.set(SESSION_COOKIE, payload.session_token, {
    httpOnly: true,
    secure,
    sameSite: "strict",
    path: "/",
    maxAge,
  });
  response.cookies.set(CSRF_COOKIE, payload.csrf_token, {
    httpOnly: false,
    secure,
    sameSite: "strict",
    path: "/",
    maxAge,
  });
  return response;
}
