/**
 * Gateway client.
 *
 * Requests are proxied through Next.js route handlers rather than sent from the
 * browser directly, so the admin credential stays server-side. Putting it in
 * the browser would hand every dashboard viewer the ability to rewrite budgets.
 */

import type {
  AgentSummary,
  AuditRecord,
  BudgetState,
  CatalogModel,
  EventItem,
  LedgerEntry,
  PlaygroundRunRequest,
  PlaygroundRunResponse,
  ProviderConfig,
  ProviderTestResult,
  ProviderUpdateRequest,
  Readiness,
  SessionView,
  TeamSummary,
} from "./types";

const GATEWAY_URL = (
  process.env.GATEWAY_URL ?? "http://localhost:8080"
).replace(/\/$/, "");
const ADMIN_KEY = process.env.ABC_ADMIN_API_KEY ?? "local-admin-key";

export class GatewayError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${GATEWAY_URL}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${ADMIN_KEY}`,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    // Budget figures are worthless if they are stale.
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    throw new GatewayError(
      `${init.method ?? "GET"} ${path} failed: ${body.slice(0, 300)}`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export async function getReadiness(): Promise<Readiness> {
  return request<Readiness>("/readyz");
}

export async function getBudget(
  scope: string,
  scopeId: string,
): Promise<BudgetState> {
  return request<BudgetState>(
    `/v1/budgets/${scope}/${encodeURIComponent(scopeId)}`,
  );
}

export async function getLedger(
  agentId?: string,
  limit = 50,
): Promise<LedgerEntry[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (agentId) params.set("agent_id", agentId);
  const body = await request<{ entries: LedgerEntry[] }>(
    `/v1/ledger?${params}`,
  );
  return body.entries;
}

export async function getAudit(): Promise<AuditRecord[]> {
  const body = await request<{ events: AuditRecord[] }>("/v1/admin/audit");
  return body.events;
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

export async function getTeams(): Promise<TeamSummary[]> {
  return request<TeamSummary[]>("/v1/teams");
}

export async function getTeam(teamId: string): Promise<TeamSummary> {
  return request<TeamSummary>(`/v1/teams/${encodeURIComponent(teamId)}`);
}

export async function createTeam(payload: {
  team_id: string;
  budget: {
    amount_usd: string;
    window?: string;
    warning_percent?: number;
  };
}): Promise<{ team_id: string; limit_usd: string }> {
  return request<{ team_id: string; limit_usd: string }>("/v1/teams", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateTeamBudget(
  teamId: string,
  spec: {
    amount_usd: string;
    window?: string;
    warning_percent?: number;
  },
): Promise<{ team_id: string; limit_usd: string }> {
  return request<{ team_id: string; limit_usd: string }>(
    `/v1/teams/${encodeURIComponent(teamId)}/budget`,
    {
      method: "PUT",
      body: JSON.stringify(spec),
    },
  );
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export async function getAgents(): Promise<AgentSummary[]> {
  return request<AgentSummary[]>("/v1/agents");
}

export async function getAgent(agentId: string): Promise<AgentSummary> {
  return request<AgentSummary>(`/v1/agents/${encodeURIComponent(agentId)}`);
}

export async function createAgent(payload: {
  agent_id: string;
  team_id: string;
  budget: {
    amount_usd: string;
    window?: string;
    warning_percent?: number;
  };
  routing: {
    provider: string;
    preferred_model: string;
    fallback_models?: string[];
  };
  session_budget_usd?: string;
  default_max_output_tokens?: number;
}): Promise<{ agent_id: string; limit_usd: string }> {
  return request<{ agent_id: string; limit_usd: string }>("/v1/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAgentBudget(
  agentId: string,
  spec: {
    amount_usd: string;
    window?: string;
    warning_percent?: number;
  },
): Promise<{ agent_id: string; limit_usd: string }> {
  return request<{ agent_id: string; limit_usd: string }>(
    `/v1/agents/${encodeURIComponent(agentId)}/budget`,
    {
      method: "PUT",
      body: JSON.stringify(spec),
    },
  );
}

export async function updateAgentRouting(
  agentId: string,
  spec: {
    provider: string;
    preferred_model: string;
    fallback_models?: string[];
    allow_fallback?: boolean;
  },
): Promise<{ agent_id: string; preferred_model: string }> {
  return request<{ agent_id: string; preferred_model: string }>(
    `/v1/agents/${encodeURIComponent(agentId)}/routing-policy`,
    {
      method: "PUT",
      body: JSON.stringify(spec),
    },
  );
}

export async function pauseAgent(
  agentId: string,
  reason: string,
): Promise<AuditRecord> {
  return request<AuditRecord>(
    `/v1/admin/agents/${encodeURIComponent(agentId)}/pause`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export async function resumeAgent(
  agentId: string,
  reason: string,
): Promise<AuditRecord> {
  return request<AuditRecord>(
    `/v1/admin/agents/${encodeURIComponent(agentId)}/resume`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export async function getSessions(agentId?: string): Promise<SessionView[]> {
  const params = new URLSearchParams();
  if (agentId) params.set("agent_id", agentId);
  return request<SessionView[]>(`/v1/sessions?${params}`);
}

export async function createSession(payload: {
  session_id?: string;
  ttl_seconds?: number;
}): Promise<SessionView> {
  return request<SessionView>("/v1/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function closeSession(sessionId: string): Promise<SessionView> {
  return request<SessionView>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/close`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

export async function getEvents(limit = 100): Promise<EventItem[]> {
  return request<EventItem[]>(`/v1/events?limit=${limit}`);
}

// ---------------------------------------------------------------------------
// Playground
// ---------------------------------------------------------------------------

export async function runPlaygroundChat(
  payload: PlaygroundRunRequest,
): Promise<PlaygroundRunResponse> {
  return request<PlaygroundRunResponse>("/v1/playground/chat", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Provider configuration (admin)
// ---------------------------------------------------------------------------

export async function getProviders(): Promise<ProviderConfig[]> {
  return request<ProviderConfig[]>("/v1/admin/providers");
}

export async function getProvider(provider: string): Promise<ProviderConfig> {
  return request<ProviderConfig>(`/v1/admin/providers/${encodeURIComponent(provider)}`);
}

export async function updateProvider(
  provider: string,
  updates: ProviderUpdateRequest,
): Promise<ProviderConfig> {
  return request<ProviderConfig>(`/v1/admin/providers/${encodeURIComponent(provider)}`, {
    method: "PUT",
    body: JSON.stringify(updates),
  });
}

export async function enableProvider(provider: string): Promise<ProviderConfig> {
  return request<ProviderConfig>(`/v1/admin/providers/${encodeURIComponent(provider)}/enable`, {
    method: "POST",
  });
}

export async function disableProvider(provider: string): Promise<ProviderConfig> {
  return request<ProviderConfig>(`/v1/admin/providers/${encodeURIComponent(provider)}/disable`, {
    method: "POST",
  });
}

export async function testProvider(
  provider: string,
  model?: string,
): Promise<ProviderTestResult> {
  return request<ProviderTestResult>(`/v1/admin/providers/${encodeURIComponent(provider)}/test`, {
    method: "POST",
    body: JSON.stringify({ model }),
  });
}

export async function getCatalogModels(provider?: string): Promise<CatalogModel[]> {
  const params = new URLSearchParams();
  if (provider) params.set("provider", provider);
  const qs = params.toString();
  return request<CatalogModel[]>(`/v1/admin/catalog/models${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

export function usd(value: string | number, decimals = 2): string {
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(n)) return "$0.00";
  return `$${n.toFixed(decimals)}`;
}

export function tokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}
