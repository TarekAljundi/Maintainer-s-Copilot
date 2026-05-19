"""Chat endpoint. SSE streaming. Delegates to ChatbotService."""
from fastapi import APIRouter

router = APIRouter(prefix="/chat", tags=["chat"])
