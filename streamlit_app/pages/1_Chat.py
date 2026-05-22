"""Chat page. POSTs to /api/chat SSE endpoint and renders streamed tokens live.

PRD §Authentication: JWT is attached as `Authorization: Bearer …` to every
API call. user_id is derived server-side from the JWT — no longer sent in
the request body.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Iterator

import httpx
import streamlit as st

import _theme

API_BASE = os.environ.get("API_BASE", "http://api:8000")

st.set_page_config(page_title="Chat — Maintainer's Copilot")
_theme.apply()
st.title("Chat")

jwt = st.session_state.get("jwt")
if not jwt:
    st.warning("Sign in on the Home page first.")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = uuid.uuid4().hex

col1, col2 = st.columns([3, 1])
with col2:
    if st.button("New conversation"):
        st.session_state.messages = []
        st.session_state.conversation_id = uuid.uuid4().hex
        st.rerun()
    st.caption(f"conv: `{st.session_state.conversation_id[:8]}`")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])


def _stream_tokens(message: str) -> Iterator[str]:
    payload = {
        "message": message,
        "conversation_id": st.session_state.conversation_id,
    }
    headers = {"Authorization": f"Bearer {jwt}"}
    with httpx.stream(
        "POST",
        f"{API_BASE}/api/chat",
        json=payload,
        headers=headers,
        timeout=120,
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "token":
                yield event.get("content", "")


if prompt := st.chat_input("Ask anything"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        full = st.write_stream(_stream_tokens(prompt))
    st.session_state.messages.append({"role": "assistant", "content": full})
