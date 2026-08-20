/**
 * Types mirroring the gateway's API contracts.
 *
 * Money is a decimal *string* everywhere, never a number. JavaScript numbers
 * are IEEE-754 doubles, so parsing "0.040000" into a float and rendering it
 * back can produce a value that disagrees with the ledger. Strings are
 * formatted for display and compared as strings; where a numeric comparison is
 * genuinely needed, it happens on the integer nano value.
 */

export interface BudgetState {
  scope_type: string;
  scope_id: string;
  window: string;
  limit_usd: string;
  committed_usd: string;
  /** Held for requests currently in flight. Not yet spent, not available. */
  reserved_usd: string;
  /** Subset of reserved whose provider outcome is unknown. */
  pending_usd: string;
  available_usd: string;
  /** Non-zero means a provider exceeded the cap we sent. Always an anomaly. */
  overage_usd: string;
  /** Settled spend only. This is what the 80% warning fires on. */
  utilization_percent: number;
  /** Settled plus in-flight. Shows how much is already promised. */
  effective_utilization_percent: number;
  warning_sent: boolean;
  open_reservations: number;
  reset_at: string | null;
  input_tokens: number;
  output_tokens: number;
}

export interface AgentSummary {
  agent_id: string;
  team_id: string;
  status: "ACTIVE" | "PAUSED_RUNAWAY" | "PAUSED_ADMIN" | "DISABLED" | string;
  limit_usd: string;
  committed_usd: string;
  reserved_usd: string;
  available_usd: string;
  utilization_percent: number;
  effective_utilization_percent: number;
  warning_sent: boolean;
  input_tokens: number;
  output_tokens: number;
  preferred_model: string;
  fallback_models: string[];
  substitution_enabled: boolean;
  substitution_threshold_percent: number;
  session_budget_usd: string | null;
  pause_reason: string | null;
  review_required: boolean;
  window_type: string;
  default_max_output_tokens: number;
}

export interface TeamSummary {
  team_id: string;
  limit_usd: string;
  committed_usd: string;
  reserved_usd: string;
  available_usd: string;
  utilization_percent: number;
  warning_threshold_percent: number;
  window_type: string;
  agent_count: number;
  active_sessions: number;
  agents: AgentSummary[];
}

export interface LedgerEntry {
  entry_id: string;
  request_id: string;
  agent_id: string;
  session_id: string | null;
  provider: string;
  requested_model: string;
  effective_model: string;
  decision: string;
  kind: string;
  preflight_input_tokens: number;
  reserved_output_tokens: number;
  actual_input_tokens: number;
  actual_output_tokens: number;
  actual_cached_input_tokens: number;
  actual_reasoning_tokens: number;
  estimated_max_cost_usd: string;
  actual_total_cost_usd: string;
  price_catalog_version: string;
  created_at: string;
  completed_at: string | null;
}

export interface SessionView {
  session_id: string;
  agent_id: string;
  team_id?: string;
  status: string;
  limit_usd: string;
  committed_usd: string;
  available_usd: string;
  close_reason: string | null;
  opened_at?: string;
  expires_at?: string;
}

export interface EventItem {
  event_id: string;
  kind: string;
  severity: "info" | "warn" | "danger" | string;
  title: string;
  description: string;
  team_id?: string | null;
  agent_id?: string | null;
  session_id?: string | null;
  amount_usd?: string | null;
  threshold_percent?: number | null;
  occurred_at: string;
  actor?: string | null;
  metadata?: Record<string, any>;
}

export interface AuditRecord {
  actor: string;
  action: string;
  target: string;
  previous_state: string;
  new_state: string;
  reason: string;
  timestamp: string;
}

export interface Readiness {
  status: string;
  checks: Record<string, boolean>;
  detail: Record<string, string>;
}

export interface BudgetUpdateEvent {
  event_id: string;
  type: "budget.state.updated";
  scope_type: string;
  scope_id: string;
  version: number;
  committed_usd: string;
  reserved_usd: string;
  limit_usd: string;
  utilization_percent: number;
  status: string;
}

export type AgentStatus =
  | "ACTIVE"
  | "PAUSED_RUNAWAY"
  | "PAUSED_ADMIN"
  | "DISABLED";

export interface ProviderConfig {
  provider: string;
  display_name: string;
  enabled: boolean;
  configured: boolean;
  default_model: string;
  auth_type: "api_key" | "iam_role" | "none";
  masked_api_key: string | null;
  region: string | null;
  base_url: string | null;
  organization_id: string | null;
  test_params: Record<string, any>;
  connection_status: "healthy" | "unhealthy" | "untested";
  last_tested_at: string | null;
  last_error: string | null;
  is_production_ready: boolean;
  available_models: string[];
}

export interface ProviderUpdateRequest {
  enabled?: boolean;
  default_model?: string;
  api_key?: string;
  region?: string;
  base_url?: string;
  organization_id?: string;
  test_params?: Record<string, any>;
}

export interface ProviderTestResult {
  provider: string;
  status: "healthy" | "unhealthy" | "untested";
  model: string;
  authentication: string;
  checked_at: string;
  message: string;
  error_type?: string;
}

export interface CatalogModel {
  provider: string;
  model: string;
  status: string;
  input_per_million: string;
  output_per_million: string;
  cached_input_per_million: string;
  max_context_tokens: number;
  max_output_tokens: number;
  supports_tools: boolean;
  supports_structured_output: boolean;
  supports_vision: boolean;
  supports_reasoning: boolean;
  preflight_token_counting: boolean;
  catalog_version: string;
}

export interface PlaygroundLifecycleStep {
  step_number: number;
  name: string;
  description: string;
  status: "completed" | "running" | "pending" | "blocked";
  details?: Record<string, any>;
}

export interface PlaygroundRunRequest {
  agent_id: string;
  prompt: string;
  session_id?: string;
  model?: string;
  max_output_tokens?: number;
}

export interface PlaygroundRunResponse {
  request_id: string;
  agent_id: string;
  decision: string;
  status: string;
  requested_model: string;
  effective_model: string;
  substituted: boolean;
  routing_reason?: string | null;
  preflight_input_tokens: number;
  actual_input_tokens: number;
  reserved_output_tokens: number;
  actual_output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: string;
  actual_cost_usd: string;
  estimated_savings_usd?: string | null;
  response_text: string;
  lifecycle_steps: PlaygroundLifecycleStep[];
  blocked: boolean;
  block_reason?: string | null;
  provider_calls_made: number;
}

export type Role = "VIEWER" | "OPERATOR" | "ADMIN";

export interface SessionIdentity {
  user_id: string;
  email: string;
  role: Role;
  tenant_id: string;
}
