"""Read-only episodic memory list for the current user.

PRD user story 13. No inline editing, no manual recall trigger
(per §Out of Scope — Streamlit memory inspector beyond a read-only list).
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://api:8000")

st.set_page_config(page_title="Memory Inspector — Maintainer's Copilot")
st.title("Memory Inspector")

jwt = st.session_state.get("jwt")
if not jwt:
    st.warning("Sign in on the Home page first.")
    st.stop()


@st.cache_data(ttl=30, show_spinner=False)
def _fetch(token: str, limit: int) -> list[dict] | None:
    try:
        r = httpx.get(
            f"{API_BASE}/api/memory",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        st.error(f"API returned {r.status_code}: {r.text[:200]}")
    except httpx.HTTPError as exc:
        st.error(f"Network error: {exc}")
    return None


limit = st.slider("Max memories", min_value=10, max_value=500, value=100, step=10)

if st.button("Refresh"):
    _fetch.clear()

memories = _fetch(jwt, limit)

if memories is None:
    st.stop()

if not memories:
    st.info(
        "No memories yet. Tell the chatbot 'remember that I'm focused on X' "
        "to write your first one."
    )
    st.stop()

st.caption(f"Showing {len(memories)} memory row(s), newest first.")
for m in memories:
    with st.container(border=True):
        st.markdown(f"**{m['summary']}**")
        meta_bits = []
        if m.get("entities"):
            meta_bits.append("entities: " + ", ".join(m["entities"]))
        meta_bits.append(f"created: {m['created_at']}")
        if m.get("last_recalled_at"):
            meta_bits.append(f"last recalled: {m['last_recalled_at']}")
        if m.get("conversation_id"):
            meta_bits.append(f"conv: `{m['conversation_id'][:8]}`")
        st.caption(" · ".join(meta_bits))
