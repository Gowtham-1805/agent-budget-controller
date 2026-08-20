import { NextResponse } from "next/server";
import { cookies } from "next/headers";

const GATEWAY_URL = (
  process.env.GATEWAY_URL ?? "http://localhost:8080"
).replace(/\/$/, "");

const SESSION_COOKIE = "abc_dash_session";
const CSRF_COOKIE = "abc_dash_csrf";

export const dynamic = "force-dynamic";

export async function POST() {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;

  if (token) {
    await fetch(`${GATEWAY_URL}/v1/auth/logout`, {
      method: "POST",
      headers: { "X-ABC-Session": token },
      cache: "no-store",
    }).catch(() => {
      // The cookie is cleared below regardless -- a logout must never leave
      // the browser holding a cookie it believes is still live, even if the
      // gateway call itself failed.
    });
  }

  const response = new NextResponse(null, { status: 204 });
  response.cookies.delete({ name: SESSION_COOKIE, path: "/" });
  response.cookies.delete({ name: CSRF_COOKIE, path: "/" });
  return response;
}
