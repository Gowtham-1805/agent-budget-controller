"""Human authentication: login, logout, session identity, password change.

Deliberately its own router rather than folded into ``routes.py``: every
handler here resolves identity from a *session* token, not the bearer-key
path ``get_principal`` uses for agents and the admin bootstrap credential --
mixing the two would blur exactly the boundary
:meth:`~..auth.identity.Principal.require_agent` exists to keep sharp.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request, Response

from ..auth.identity import AuthenticationError, AuthorizationError, Principal, hash_key
from ..domain.user import Role, UserRecord, UserStatus, normalize_email
from . import schemas as S
from .deps import Container, get_container, get_principal

auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _extract_session_token(request: Request, container: Container) -> tuple[str | None, str]:
    """The raw session token and where it came from: "cookie" or "header".

    The distinction matters for CSRF: a cookie is attached by the browser to
    every same-origin request automatically, so a state-changing call
    authenticated that way needs the double-submit check below. A custom
    header (what the dashboard's own server-side proxy sends) cannot be
    attached by a third-party page without script access to it in the first
    place, so it needs no separate CSRF token.
    """
    cookie_token = request.cookies.get(container.settings.session_cookie_name)
    if cookie_token:
        return cookie_token, "cookie"
    header_token = request.headers.get("X-ABC-Session")
    if header_token:
        return header_token, "header"
    return None, "none"


async def get_session_principal(
    request: Request,
    container: Container = Depends(get_container),
    x_abc_csrf: str | None = Header(default=None),
) -> Principal:
    """Resolve a human session for the auth endpoints that require one.

    Separate from ``get_principal``: these endpoints (logout, password
    change, "who am I") are meaningful only for a logged-in human, never for
    an agent's API key, so there is nothing to gain by accepting one here.
    """
    token, source = _extract_session_token(request, container)
    if not token:
        raise AuthenticationError("missing credential")
    principal = await container.sessions.resolve(token)

    if source == "cookie" and request.method not in ("GET", "HEAD"):
        session = await container.sessions.get_session_for_csrf(token)
        if session is None:
            raise AuthenticationError("session expired or revoked")
        container.sessions.verify_csrf(session, x_abc_csrf)

    request.state.session_token = token
    return principal


def _set_session_cookies(response: Response, container: Container, token: str, csrf: str) -> None:
    settings = container.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.environment != "local",
        samesite="strict",
        path="/",
        max_age=settings.session_ttl_minutes * 60,
    )
    # Not HttpOnly, by design: the double-submit pattern requires JS on the
    # same origin to be able to read this value and echo it back as a header.
    response.set_cookie(
        key=settings.session_csrf_cookie_name,
        value=csrf,
        httponly=False,
        secure=settings.environment != "local",
        samesite="strict",
        path="/",
        max_age=settings.session_ttl_minutes * 60,
    )


def _clear_session_cookies(response: Response, container: Container) -> None:
    settings = container.settings
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.session_csrf_cookie_name, path="/")


@auth_router.post("/login", response_model=S.LoginResponse)
async def login(
    body: S.LoginRequest,
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
) -> S.LoginResponse:
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "")

    try:
        raw_token, session = await container.sessions.login(
            email=str(body.email), password=body.password, ip=client_ip, user_agent=user_agent
        )
    except AuthenticationError:
        # Re-raised as-is: the registered handler for AuthenticationError
        # already produces a generic 401, which is exactly what every failure
        # mode here (unknown email, wrong password, locked account) must
        # share -- see auth/sessions.py's module docstring.
        raise

    _set_session_cookies(response, container, raw_token, session.csrf_token)
    return S.LoginResponse(
        user_id=session.user_id,
        email=str(body.email),
        role=session.role.value,
        tenant_id=session.tenant_id,
        expires_at=session.expires_at.isoformat(),
        session_token=raw_token,
        csrf_token=session.csrf_token,
    )


@auth_router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    container: Container = Depends(get_container),
) -> None:
    token, _source = _extract_session_token(request, container)
    if token:
        await container.sessions.logout(token)
    _clear_session_cookies(response, container)


@auth_router.get("/session", response_model=S.SessionIdentityResponse)
async def get_session_identity(
    principal: Principal = Depends(get_session_principal),
    container: Container = Depends(get_container),
) -> S.SessionIdentityResponse:
    assert principal.user_id is not None  # guaranteed by get_session_principal
    user = await container.repository.get_user(principal.tenant_id, principal.user_id)
    if user is None:
        raise AuthenticationError("session expired or revoked")
    return S.SessionIdentityResponse(
        user_id=user.user_id,
        email=user.email,
        role=user.role.value,
        tenant_id=user.tenant_id,
        issued_at=user.password_changed_at.isoformat(),
        expires_at="",
    )


@auth_router.post("/password", status_code=204)
async def change_password(
    body: S.ChangePasswordRequest,
    request: Request,
    principal: Principal = Depends(get_session_principal),
    container: Container = Depends(get_container),
) -> None:
    assert principal.user_id is not None
    user = await container.repository.get_user(principal.tenant_id, principal.user_id)
    if user is None:
        raise AuthenticationError("session expired or revoked")

    if not container.sessions.passwords.verify(user.password_hash, body.current_password):
        raise AuthenticationError("invalid email or password")

    updated = replace(
        user,
        password_hash=container.sessions.passwords.hash(body.new_password),
        password_changed_at=datetime.now(UTC),
    )
    await container.repository.put_user(updated)

    # Every other session for this account is a stale credential the moment
    # the password changes -- keep only the one that just proved it knows the
    # new password.
    current_token = getattr(request.state, "session_token", None)
    await container.sessions.revoke_all_for_user(
        user.tenant_id, user.user_id, except_raw_token=current_token
    )


# ---------------------------------------------------------------------------
# Admin user management
# ---------------------------------------------------------------------------


def _to_user_response(user: UserRecord) -> S.UserResponse:
    return S.UserResponse(
        user_id=user.user_id,
        email=user.email,
        role=user.role.value,
        status=user.status.value,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        created_at=user.created_at.isoformat(),
    )


@auth_router.post("/admin/users", status_code=201, response_model=S.UserResponse)
async def create_user(
    body: S.CreateUserRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.UserResponse:
    principal.require_role(Role.ADMIN)
    now = datetime.now(UTC)
    normalized = normalize_email(str(body.email))
    user = UserRecord(
        user_id=uuid4().hex,
        tenant_id=principal.tenant_id,
        email=normalized,
        email_hash=hash_key(normalized),
        password_hash=container.sessions.passwords.hash(body.password),
        role=Role(body.role),
        status=UserStatus.ACTIVE,
        created_at=now,
        password_changed_at=now,
        display_name=body.display_name,
    )
    created = await container.repository.create_user(user)
    if not created:
        raise AuthorizationError("an account with this email already exists")
    return _to_user_response(user)


@auth_router.get("/admin/users", response_model=list[S.UserResponse])
async def list_users(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[S.UserResponse]:
    principal.require_role(Role.ADMIN)
    users = await container.repository.list_users(principal.tenant_id)
    return [_to_user_response(u) for u in users]


@auth_router.patch("/admin/users/{user_id}", response_model=S.UserResponse)
async def update_user(
    user_id: str,
    body: S.UpdateUserRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.UserResponse:
    principal.require_role(Role.ADMIN)
    user = await container.repository.get_user(principal.tenant_id, user_id)
    if user is None:
        raise LookupError(f"unknown user: {user_id}")

    updates: dict[str, object] = {}
    if body.role is not None:
        updates["role"] = Role(body.role)
    if body.status is not None:
        updates["status"] = UserStatus(body.status)
    if body.password is not None:
        updates["password_hash"] = container.sessions.passwords.hash(body.password)
        updates["password_changed_at"] = datetime.now(UTC)

    updated = replace(user, **updates)
    await container.repository.put_user(updated)

    # A demotion, disable, or forced password reset must take effect
    # immediately, not whenever the user's existing sessions happen to
    # expire on their own.
    if body.role is not None or body.status is not None or body.password is not None:
        await container.sessions.revoke_all_for_user(user.tenant_id, user.user_id)

    return _to_user_response(updated)


@auth_router.post("/admin/users/{user_id}/unlock", status_code=204)
async def unlock_user(
    user_id: str,
    body: S.AdminActionRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> None:
    principal.require_role(Role.ADMIN)
    user = await container.repository.get_user(principal.tenant_id, user_id)
    if user is None:
        raise LookupError(f"unknown user: {user_id}")
    await container.repository.clear_login_failures(user.email_hash)


@auth_router.delete("/admin/users/{user_id}/sessions")
async def revoke_user_sessions(
    user_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, int]:
    principal.require_role(Role.ADMIN)
    user = await container.repository.get_user(principal.tenant_id, user_id)
    if user is None:
        raise LookupError(f"unknown user: {user_id}")
    revoked = await container.sessions.revoke_all_for_user(user.tenant_id, user.user_id)
    return {"revoked": revoked}
