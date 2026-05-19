"""Widget endpoints: /widget/{id}/config, /session, /embed (with CSP frame-ancestors)."""
from fastapi import APIRouter

router = APIRouter(prefix="/widget", tags=["widget"])
