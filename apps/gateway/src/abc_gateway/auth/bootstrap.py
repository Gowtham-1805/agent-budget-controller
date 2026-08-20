"""First-run seeding of a human admin account.

Without this, a fresh deployment with no persisted users and no
``ABC_ADMIN_API_KEY`` would have no way to reach the dashboard at all -- there
would be a login page and nothing that could ever log into it. Seeding runs
from the application lifespan (an async context), not from
``build_container`` (a synchronous one), because it needs to read and write
through the repository.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ..domain.user import Role, UserRecord, UserStatus, normalize_email
from ..observability.logging import get_logger
from .identity import hash_key
from .passwords import PasswordService

if TYPE_CHECKING:
    from ..api.deps import Container

logger = get_logger("abc_gateway.auth.bootstrap")


async def seed_bootstrap_admin(container: Container) -> None:
    """Create the first admin user if configured and not already present."""
    settings = container.settings
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return

    normalized = normalize_email(settings.bootstrap_admin_email)
    email_hash = hash_key(normalized)
    existing = await container.repository.get_user_by_email_hash(email_hash)
    if existing is not None:
        return

    passwords = PasswordService(settings)
    now = datetime.now(UTC)
    user = UserRecord(
        user_id=uuid4().hex,
        tenant_id="admin",
        email=normalized,
        email_hash=email_hash,
        password_hash=passwords.hash(settings.bootstrap_admin_password),
        role=Role.ADMIN,
        status=UserStatus.ACTIVE,
        created_at=now,
        password_changed_at=now,
        display_name="Bootstrap Admin",
    )
    created = await container.repository.create_user(user)
    if created:
        logger.info("auth.bootstrap_admin_seeded", email=normalized, tenant_id="admin")
