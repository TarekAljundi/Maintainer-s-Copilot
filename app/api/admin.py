"""Admin endpoints: widget config CRUD, user invites, audit log view."""

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])
