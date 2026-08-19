"""Attribute names, shared by every backend.

One module so that the in-memory backend and the DynamoDB backend cannot drift
apart. If a name changes here it changes for both, and the contract suite --
which runs identically against both -- keeps them honest.

Every money attribute holds an integer count of nano-USD; every token attribute
holds an integer count of tokens. Nothing here is ever a float.
"""

from __future__ import annotations

from typing import Final

# -- identity ---------------------------------------------------------------
PK: Final = "PK"
SK: Final = "SK"
GSI1PK: Final = "GSI1PK"
GSI1SK: Final = "GSI1SK"
GSI2PK: Final = "GSI2PK"
GSI2SK: Final = "GSI2SK"
ENTITY_TYPE: Final = "entity_type"

# -- entity types -----------------------------------------------------------
E_BUDGET_STATE: Final = "BUDGET_STATE"
E_BUDGET_POLICY: Final = "BUDGET_POLICY"
E_AGENT_POLICY: Final = "AGENT_POLICY"
E_AGENT_STATE: Final = "AGENT_STATE"
E_SESSION: Final = "SESSION"
E_RESERVATION: Final = "RESERVATION"
E_IDEMPOTENCY: Final = "IDEMPOTENCY"
E_ALERT_EVENT: Final = "ALERT_EVENT"
E_LEDGER_ENTRY: Final = "LEDGER_ENTRY"
E_AUDIT_EVENT: Final = "AUDIT_EVENT"
E_ROLLING_BUCKET: Final = "ROLLING_BUCKET"
E_ROLLING_MARK: Final = "ROLLING_MARK"

# -- budget counters --------------------------------------------------------
# `remaining_*` is the materialised counter the authorization condition reads.
# It exists because DynamoDB condition expressions have no arithmetic: you
# cannot write `committed + reserved + :cost <= limit`, but you can write
# `remaining_nano >= :cost`.
REMAINING_NANO: Final = "remaining_nano"
REMAINING_INPUT: Final = "remaining_input_tokens"
REMAINING_OUTPUT: Final = "remaining_output_tokens"
REMAINING_TOTAL: Final = "remaining_total_tokens"

LIMIT_NANO: Final = "limit_nano"
MAX_INPUT: Final = "max_input_tokens"
MAX_OUTPUT: Final = "max_output_tokens"
MAX_TOTAL: Final = "max_total_tokens"

COMMITTED_NANO: Final = "committed_nano"
COMMITTED_INPUT: Final = "committed_input_tokens"
COMMITTED_OUTPUT: Final = "committed_output_tokens"
COMMITTED_TOTAL: Final = "committed_total_tokens"

RESERVED_NANO: Final = "reserved_nano"
RESERVED_INPUT: Final = "reserved_input_tokens"
RESERVED_OUTPUT: Final = "reserved_output_tokens"
RESERVED_TOTAL: Final = "reserved_total_tokens"

#: Subset of reserved whose provider outcome is unknown. Reporting only -- it is
#: not deducted a second time.
PENDING_NANO: Final = "pending_nano"
#: Actual spend beyond its reservation. Only ever grows. Non-zero means a
#: provider overshot the hard cap we sent, and it is alarmed rather than hidden.
OVERAGE_NANO: Final = "overage_nano"

OPEN_RESERVATIONS: Final = "open_reservations"
WARNING_80_SENT: Final = "warning_80_sent"
WARNING_100_SENT: Final = "warning_100_sent"
POLICY_VERSION: Final = "policy_version"
VERSION: Final = "version"

WINDOW_START_EPOCH: Final = "window_start_epoch"
WINDOW_END_EPOCH: Final = "window_end_epoch"
WINDOW_TYPE: Final = "window_type"
WINDOW_ID: Final = "window_id"
SCOPE_TYPE: Final = "scope_type"
SCOPE_ID: Final = "scope_id"
TENANT_ID: Final = "tenant_id"
UPDATED_AT: Final = "updated_at_epoch"

#: TTL is for housekeeping only. Authorization never reads it, because
#: DynamoDB deletes expired items only eventually and a budget must reset on
#: the stroke of the boundary, not days later.
HOUSEKEEPING_TTL: Final = "housekeeping_ttl"

# -- status attributes ------------------------------------------------------
STATUS: Final = "status"
EXPIRES_AT_EPOCH: Final = "expires_at_epoch"
DISPATCH_STATE: Final = "dispatch_state"
STATE: Final = "state"

#: Groups of counters, so backends iterate the same fields in the same order.
MONEY_TRIPLE: Final = (REMAINING_NANO, COMMITTED_NANO, RESERVED_NANO)

TOKEN_FIELDS: Final = (
    (REMAINING_INPUT, COMMITTED_INPUT, RESERVED_INPUT, MAX_INPUT, "input"),
    (REMAINING_OUTPUT, COMMITTED_OUTPUT, RESERVED_OUTPUT, MAX_OUTPUT, "output"),
    (REMAINING_TOTAL, COMMITTED_TOTAL, RESERVED_TOTAL, MAX_TOTAL, "total"),
)
