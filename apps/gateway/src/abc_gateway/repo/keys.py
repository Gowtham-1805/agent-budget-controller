"""Key construction: the single source of truth for every item address.

Both backends build keys through this module. Key formats are effectively part
of the storage contract -- once data exists, changing one is a migration -- so
they live in exactly one place rather than being spelled out at each call site.

Note where items are deliberately kept *apart*. An agent's status lives on
``SK=STATE`` while its budget counters live on ``SK=WINDOW#...``; a session's
status lives on ``SK=META`` while its counters live under a different partition
entirely. That separation is not incidental: DynamoDB forbids two actions
against the same item inside one transaction, and authorization needs to check
status *and* decrement counters in the same transaction. Splitting the items is
what makes that legal.
"""

from __future__ import annotations

from typing import Final

from ..domain.alerts import AlertEvent
from ..domain.scopes import ScopeRef
from ..domain.window import BudgetWindow
from .plans import ItemKey

TABLE_CORE: Final = "core"
TABLE_LEDGER: Final = "ledger"

AGENT_STATE_SK: Final = "STATE"
AGENT_POLICY_SK: Final = "POLICY#CURRENT"
ROUTING_SK: Final = "ROUTING#CURRENT"
SESSION_META_SK: Final = "META"
RESERVATION_SK: Final = "RESERVATION"
IDEMPOTENCY_SK: Final = "IDEM"
BUDGET_POLICY_SK: Final = "POLICY#CURRENT"


def _tenant(tenant_id: str) -> str:
    return f"TNT#{tenant_id}"


# -- budget counters --------------------------------------------------------


def budget_pk(tenant_id: str, scope: ScopeRef) -> str:
    return f"{_tenant(tenant_id)}#BUDGET#{scope.key()}"


def budget_state_key(tenant_id: str, scope: ScopeRef, window: BudgetWindow) -> ItemKey:
    """The counter item for one scope in one window.

    The window is part of the key, so a new period is addressed the instant it
    begins rather than waiting for the old item to be cleaned up.
    """
    return ItemKey(TABLE_CORE, budget_pk(tenant_id, scope), window.sort_key())


def budget_policy_key(tenant_id: str, scope: ScopeRef) -> ItemKey:
    return ItemKey(TABLE_CORE, budget_pk(tenant_id, scope), BUDGET_POLICY_SK)


# -- agent ------------------------------------------------------------------


def agent_pk(tenant_id: str, agent_id: str) -> str:
    return f"{_tenant(tenant_id)}#AGENT#{agent_id}"


def agent_state_key(tenant_id: str, agent_id: str) -> ItemKey:
    """Status only. Separate from the agent's budget counters by design."""
    return ItemKey(TABLE_CORE, agent_pk(tenant_id, agent_id), AGENT_STATE_SK)


def agent_policy_key(tenant_id: str, agent_id: str) -> ItemKey:
    return ItemKey(TABLE_CORE, agent_pk(tenant_id, agent_id), AGENT_POLICY_SK)


# -- session ----------------------------------------------------------------


def session_pk(tenant_id: str, session_id: str) -> str:
    return f"{_tenant(tenant_id)}#SESSION#{session_id}"


def session_meta_key(tenant_id: str, session_id: str) -> ItemKey:
    """Lifecycle status only; counters live under the budget partition."""
    return ItemKey(TABLE_CORE, session_pk(tenant_id, session_id), SESSION_META_SK)


# -- reservation and idempotency -------------------------------------------


def reservation_key(tenant_id: str, reservation_id: str) -> ItemKey:
    return ItemKey(TABLE_CORE, f"{_tenant(tenant_id)}#REQ#{reservation_id}", RESERVATION_SK)


def idempotency_key(tenant_id: str, key_hash: str) -> ItemKey:
    """Durable idempotency, independent of any transaction client token.

    DynamoDB's own ``ClientRequestToken`` expires after minutes; a client may
    retry a request hours later. This item is the durable record that stops the
    retry becoming a second charge.
    """
    return ItemKey(TABLE_CORE, f"{_tenant(tenant_id)}#IDEM#{key_hash}", IDEMPOTENCY_SK)


# -- alerts -----------------------------------------------------------------


def alert_key(tenant_id: str, alert: AlertEvent) -> ItemKey:
    """Deterministic, so a conditional put is itself an exactly-once guarantee.

    A duplicate attempt collides with the existing item instead of creating a
    second alert, independently of whether the threshold flag flipped.
    """
    return ItemKey(TABLE_CORE, budget_pk(tenant_id, alert.scope), alert.sort_key())


# -- ledger -----------------------------------------------------------------


def ledger_key(
    tenant_id: str,
    agent_id: str,
    *,
    year_month: str,
    created_at_micros: int,
    reservation_id: str,
    sequence: int,
) -> ItemKey:
    """Append-only ledger address.

    Partitioned by agent and month to keep a single partition from growing
    without bound; sorted by timestamp so a time-ranged audit query is a range
    scan rather than a filter.
    """
    return ItemKey(
        TABLE_LEDGER,
        f"{_tenant(tenant_id)}#LEDGER#{agent_id}#{year_month}",
        f"{created_at_micros:020d}#{reservation_id}#{sequence}",
    )


def audit_key(tenant_id: str, day: str, ordinal: str) -> ItemKey:
    return ItemKey(TABLE_LEDGER, f"{_tenant(tenant_id)}#AUDIT#{day}", ordinal)


# -- rolling spend (runaway detection) --------------------------------------


def rolling_bucket_key(tenant_id: str, agent_id: str, minute: str) -> ItemKey:
    """One-minute spend bucket.

    Minute granularity lets a rolling 60-minute total be summed from 60 items.
    Calendar-hour buckets would be cheaper but blind to a burst that straddles
    the hour boundary -- which is the shape a runaway loop actually produces.
    """
    return ItemKey(TABLE_CORE, agent_pk(tenant_id, agent_id), f"ROLL#{minute}")


def rolling_mark_key(tenant_id: str, entry_id: str) -> ItemKey:
    """Dedup marker for at-least-once stream delivery."""
    return ItemKey(TABLE_CORE, f"{_tenant(tenant_id)}#ROLLMARK#{entry_id}", "MARK")


# -- human auth ---------------------------------------------------------
#
# The email index is deliberately *not* tenant-prefixed: a login form has an
# email and a password and no tenant selector, so the tenant must come *out
# of* the lookup rather than being known in advance. That also makes email
# uniqueness a global property, which is what one login form implies.
#
# Sessions use the ``USERSESSION``/``AUTH#SESSION`` prefixes rather than the
# existing ``SESSION`` prefix that agent conversation sessions already own
# (see ``session_pk`` above) -- reusing that prefix would collide with agent
# sessions in ``list_sessions``'s entity-type scan.


def user_key(tenant_id: str, user_id: str) -> ItemKey:
    return ItemKey(TABLE_CORE, f"{_tenant(tenant_id)}#USER#{user_id}", "PROFILE")


def user_email_index_key(email_hash: str) -> ItemKey:
    return ItemKey(TABLE_CORE, f"AUTH#EMAIL#{email_hash}", "LOOKUP")


def auth_session_key(token_hash: str) -> ItemKey:
    return ItemKey(TABLE_CORE, f"AUTH#SESSION#{token_hash}", "SESSION")


def login_counter_key(email_hash: str) -> ItemKey:
    return ItemKey(TABLE_CORE, f"AUTH#LOGINFAIL#{email_hash}", "COUNTER")


def credentials_marker_key() -> ItemKey:
    """A single fixed item, written once the first user or API key exists.

    Lets ``has_any_credential()`` answer with one bounded ``GetItem`` instead
    of a table scan on every ``/readyz`` probe.
    """
    return ItemKey(TABLE_CORE, "AUTH#CREDENTIALS", "MARKER")


def api_key_record_key(key_hash: str) -> ItemKey:
    """Tenant-less, like the email index and the session key.

    This lookup runs on *every* data-plane request (``get_principal`` resolves
    a bearer credential on the hot path), so it must be a direct O(1) item
    get -- never a scan. The tenant is not known until after the record is
    read; it lives inside the item body, not the key.
    """
    return ItemKey(TABLE_CORE, f"AUTH#APIKEY#{key_hash}", "KEY")
