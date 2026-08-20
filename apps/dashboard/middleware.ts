import { NextRequest, NextResponse } from "next/server";
import { safeNext } from "./lib/redirect";

const SESSION_COOKIE = "abc_dash_session";

/**
 * A cheap cookie-*presence* gate only -- it never validates the session.
 *
 * This runs on the edge runtime on every navigation, so a gateway round trip
 * here would be both slow and a new failure mode. The authoritative check is
 * `app/(app)/layout.tsx`'s server-side `getSession()`, which redirects one
 * hop later if the cookie turns out to be forged, expired, or revoked --
 * defence in depth, not a single point of validation.
 */
export function middleware(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.next();
  }

  const url = request.nextUrl.clone();
  const next = safeNext(request.nextUrl.pathname + request.nextUrl.search);
  url.pathname = "/login";
  url.search = "";
  if (next !== "/") {
    url.searchParams.set("next", next);
  }
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/auth/|login).*)"],
};
