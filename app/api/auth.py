"""fastapi-users JWT routes. user/admin roles. Signing key from Vault."""
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
