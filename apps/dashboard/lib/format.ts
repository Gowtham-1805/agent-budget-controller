/**
 * Pure display formatters, split out from lib/api.ts.
 *
 * lib/api.ts imports next/headers to read the session cookie, which only
 * resolves in a server context -- Next.js refuses to bundle it into any
 * client component's module graph. usd()/tokens() have no such dependency
 * and are used directly by client components (cards, tables), so they live
 * here instead of dragging the whole gateway client into the browser bundle.
 */

export function usd(value?: string | number | null, decimals = 2): string {
  if (value === null || value === undefined || value === "") return "$0.00";
  const n = typeof value === "string" ? Number.parseFloat(value) : Number(value);
  if (Number.isNaN(n) || !Number.isFinite(n)) return "$0.00";
  return `$${n.toFixed(decimals)}`;
}

export function tokens(value?: number | null): string {
  if (value === null || value === undefined || typeof value !== "number" || Number.isNaN(value)) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}
