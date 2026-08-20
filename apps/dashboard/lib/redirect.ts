/**
 * Guards against an open redirect via the login page's `?next=` parameter.
 *
 * Applied on both the login page (before it navigates there) and the login
 * API route handler (before it redirects there) -- never trust that the
 * other caller already sanitized it.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw) return "/";

  let value: string;
  try {
    value = decodeURIComponent(raw);
  } catch {
    return "/";
  }

  if (!value.startsWith("/")) return "/"; // not a local path at all
  if (value.startsWith("//") || value.startsWith("/\\")) return "/"; // protocol-relative
  if (/^\/+[a-z][a-z0-9+.-]*:/i.test(value)) return "/"; // e.g. "/https:evil.com"
  if (value.includes("\n") || value.includes("\r")) return "/"; // header injection

  return value;
}
