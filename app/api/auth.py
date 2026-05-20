"""fastapi-users JWT routes + custom principal dep.

PRD §Authentication:
- email + password registration
- JWT signing key loaded from Vault `shared/jwt` (NOT .env)
- two roles: 'user' and 'admin' (stored on the user row)
- JWT `sub` accepts `user:<uuid>` (real user) and `widget_session:<uuid>`
  (anon widget — minted by slice 13)
- session:revoked:{jti} Redis key honored on logout

This module exposes:
- `auth_backend` — bearer transport + JWT strategy
- `fastapi_users` — main fastapi-users instance
- `router` — mounted at /auth in app.main
- `current_user` — Depends() that resolves an authed User (rejects widgets)
- `current_principal` — accepts User OR AnonWidgetSession (slice 13 will mint)
- `require_admin` — current_user with role=='admin' check
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase

from app.domain.exceptions import PermissionDenied
from app.infra.vault import get_vault
from app.repositories.users import User, get_user_db


# --- Pydantic schemas exposed by fastapi-users routes ---------------------


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: str = "user"


class UserCreate(schemas.BaseUserCreate):
    role: str = "user"


class UserUpdate(schemas.BaseUserUpdate):
    role: Optional[str] = None


# --- UserManager: bridges fastapi-users <-> our DB layer -----------------


def _jwt_secret() -> str:
    secrets = get_vault().cached("shared/jwt")
    key = secrets.get("signing_key") or ""
    if not key:
        raise RuntimeError("shared/jwt.signing_key missing from vault")
    return key


JWT_LIFETIME_SECONDS = 60 * 60 * 24  # 24h, matches Redis conv sliding TTL


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    @property
    def reset_password_token_secret(self) -> str:
        return _jwt_secret()

    @property
    def verification_token_secret(self) -> str:
        return _jwt_secret()

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        # Audit-log the registration. Import locally to avoid a cycle with the
        # repository layer at import time.
        from app.repositories.audit import write_audit

        await write_audit(
            actor=str(user.id),
            action="user_register",
            target_type="user",
            target_id=str(user.id),
            payload={"email": user.email, "role": user.role},
        )


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
):
    yield UserManager(user_db)


# --- Backend ---------------------------------------------------------------


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=_jwt_secret(), lifetime_seconds=JWT_LIFETIME_SECONDS)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])


# Standard fastapi-users deps. `current_user` will reject widget tokens because
# the SQLAlchemy lookup will fail on a non-UUID `sub`. We expose it for
# admin-only endpoints where widget sessions never apply.
current_user = fastapi_users.current_user(active=True)


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user


# --- Principal: User or anonymous widget session ---------------------------


@dataclass(frozen=True, slots=True)
class AnonWidgetSession:
    widget_session_id: str
    widget_id: str
    enabled_tools: tuple[str, ...]

    @property
    def principal_id(self) -> str:
        return f"widget_session:{self.widget_session_id}"


_bearer_scheme = HTTPBearer(auto_error=False)


async def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> User | AnonWidgetSession:
    """Decode the bearer JWT and return a User or AnonWidgetSession.

    `sub` shape:
        - `<uuid>` — fastapi-users default; resolves to a User row.
        - `widget_session:<uuid>` — slice 13 anon widget; returns AnonWidgetSession.
    """
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer")
    token = creds.credentials
    try:
        payload = pyjwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            audience="fastapi-users:auth",
        )
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {exc}"
        ) from exc

    # Check revocation list.
    jti = payload.get("jti")
    if jti and await _is_revoked(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token revoked")

    sub: str = payload.get("sub", "")
    if sub.startswith("widget_session:"):
        return AnonWidgetSession(
            widget_session_id=sub.split(":", 1)[1],
            widget_id=payload.get("widget_id", ""),
            enabled_tools=tuple(payload.get("enabled_tools", [])),
        )

    # Fall back to fastapi-users-style sub (raw UUID).
    try:
        uid = uuid.UUID(sub)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unrecognized sub claim"
        )
    user = await user_db.get(uid)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or disabled"
        )
    return user


async def _is_revoked(jti: str) -> bool:
    try:
        from app.infra.redis import get_redis
    except Exception:
        return False
    try:
        r = await get_redis()
        val = await r.get(f"session:revoked:{jti}")
        return val is not None
    except Exception:
        # Fail-open on Redis outage — would block all logged-in users otherwise.
        return False


def principal_id(principal: User | AnonWidgetSession) -> str:
    if isinstance(principal, AnonWidgetSession):
        return principal.principal_id
    return f"user:{principal.id}"


def principal_user_id(principal: User | AnonWidgetSession) -> str | None:
    """Return the user_id (UUID string) if principal is an authed User, else None.

    Episodic memory is keyed by user_id; widget sessions don't write memory in
    this slice — slice 13 will key them by widget_session_id.
    """
    if isinstance(principal, AnonWidgetSession):
        return None
    return str(principal.id)


# --- Logout: revoke jti ---------------------------------------------------


router = APIRouter()

router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)


@router.post("/auth/jwt/revoke", tags=["auth"])
async def revoke(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """Add the current token's jti to `session:revoked:{jti}` with TTL = remaining lifetime.

    fastapi-users' default JWT strategy doesn't mint a `jti` claim. We honor a
    `jti` if present (e.g. widget tokens minted by slice 13), and silently no-op
    otherwise — frontend just discards the token.
    """
    if creds is None:
        return {"ok": True, "revoked": False, "reason": "no_token"}
    try:
        payload = pyjwt.decode(
            creds.credentials,
            _jwt_secret(),
            algorithms=["HS256"],
            audience="fastapi-users:auth",
            options={"verify_exp": False},
        )
    except pyjwt.InvalidTokenError:
        return {"ok": True, "revoked": False, "reason": "invalid"}

    jti = payload.get("jti")
    if not jti:
        return {"ok": True, "revoked": False, "reason": "no_jti"}

    exp = payload.get("exp")
    import time as _time

    ttl = max(1, int(exp - _time.time())) if exp else JWT_LIFETIME_SECONDS

    try:
        from app.infra.redis import get_redis

        r = await get_redis()
        await r.set(f"session:revoked:{jti}", "1", ex=ttl)
    except Exception:
        return {"ok": True, "revoked": False, "reason": "redis_unavailable"}

    return {"ok": True, "revoked": True}


@router.get("/api/me", tags=["users"])
async def me(principal=Depends(current_principal)) -> dict[str, Any]:
    if isinstance(principal, AnonWidgetSession):
        return {
            "kind": "widget",
            "widget_session_id": principal.widget_session_id,
            "widget_id": principal.widget_id,
        }
    return {
        "kind": "user",
        "id": str(principal.id),
        "email": principal.email,
        "role": principal.role,
        "is_active": principal.is_active,
    }
