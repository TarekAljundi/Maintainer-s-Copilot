"""Admin-only. CRUD widget configs (allowed_origins, theme, greeting,
enabled_tools). Shows the generated <script> embed snippet per widget.

Calls the slice-13 admin endpoints under /api/admin/widgets. Page is gated
on `me.role == "admin"` from /api/me; non-admins see an error.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st


API_BASE = os.environ.get("API_BASE", "http://api:8000")
ALLOWED_POSITIONS = ("br", "bl", "tr", "tl")
DEFAULT_TOOLS = (
    "classify_issue",
    "extract_entities",
    "summarize_thread",
    "search_knowledge",
    "write_memory",
)

st.set_page_config(page_title="Widgets — Maintainer's Copilot")
st.title("Widgets")

jwt = st.session_state.get("jwt")
me = st.session_state.get("me") or {}
if not jwt:
    st.warning("Sign in on the Home page first.")
    st.stop()
if me.get("role") != "admin":
    st.error(
        f"Admin only. Current role: `{me.get('role', '?')}`. Ask an admin to promote your account."
    )
    st.stop()


def _h() -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}


def _api(method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, f"{API_BASE}{path}", headers=_h(), timeout=15, **kw)


def _list_widgets() -> list[dict[str, Any]]:
    r = _api("GET", "/api/admin/widgets")
    if r.status_code != 200:
        st.error(f"List failed: {r.status_code} {r.text[:200]}")
        return []
    return r.json()


def _embed_snippet(wid: str) -> str | None:
    r = _api("GET", f"/api/admin/widgets/{wid}/embed-snippet")
    if r.status_code != 200:
        st.error(f"Snippet failed: {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("snippet")


def _csv_to_list(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


# ---- Create form -----------------------------------------------------------

with st.expander("Create a widget", expanded=False):
    with st.form("create_widget"):
        name = st.text_input("Name", placeholder="pandas-host-demo")
        origins = st.text_input(
            "Allowed origins (comma-separated)",
            placeholder="http://localhost:8087, https://example.com",
            help="Each entry becomes a frame-ancestors source + a CORS allowlist entry.",
        )
        c1, c2 = st.columns(2)
        with c1:
            position = st.selectbox(
                "Position",
                ALLOWED_POSITIONS,
                index=0,
                help="br/bl/tr/tl — corner of the host page the iframe pins to.",
            )
        with c2:
            primary_color = st.text_input("Primary color", value="#1e293b")
        greeting = st.text_area(
            "Greeting text",
            value="Hi! Ask me anything about this project.",
            height=80,
        )
        tools_picked = st.multiselect(
            "Enabled tools",
            list(DEFAULT_TOOLS),
            default=list(DEFAULT_TOOLS),
            help="Snapshotted into each minted anon-session JWT at /widget/{id}/session.",
        )
        submitted = st.form_submit_button("Create")
    if submitted:
        if not name.strip():
            st.error("Name is required.")
        else:
            r = _api(
                "POST",
                "/api/admin/widgets",
                json={
                    "name": name.strip(),
                    "allowed_origins": _csv_to_list(origins),
                    "primary_color": primary_color.strip() or "#1e293b",
                    "position": position,
                    "greeting_text": greeting,
                    "enabled_tools": tools_picked or list(DEFAULT_TOOLS),
                },
            )
            if r.status_code == 201:
                st.success(f"Created `{r.json()['id']}`.")
                st.rerun()
            else:
                st.error(f"Create failed: {r.status_code} {r.text[:300]}")


# ---- List + edit -----------------------------------------------------------

st.divider()
st.subheader("Existing widgets")

widgets = _list_widgets()
if not widgets:
    st.caption("None yet. Use the form above to create one.")
    st.stop()

for w in widgets:
    wid = w["id"]
    with st.expander(f"**{w['name']}**  ·  `{wid[:8]}…`", expanded=False):
        st.markdown(
            f"- **Position:** `{w['position']}`  ·  "
            f"**Color:** `{w['primary_color']}`  ·  "
            f"**Tools:** {len(w['enabled_tools'])}"
        )

        snippet = _embed_snippet(wid)
        if snippet:
            st.markdown("**Embed snippet** (copy into the host page):")
            st.code(snippet, language="html")

        st.markdown("**Edit**")
        with st.form(f"edit_{wid}"):
            e_name = st.text_input("Name", value=w["name"], key=f"n_{wid}")
            e_origins = st.text_input(
                "Allowed origins (comma-separated)",
                value=", ".join(w["allowed_origins"]),
                key=f"o_{wid}",
            )
            c1, c2 = st.columns(2)
            with c1:
                e_position = st.selectbox(
                    "Position",
                    ALLOWED_POSITIONS,
                    index=ALLOWED_POSITIONS.index(w["position"]),
                    key=f"p_{wid}",
                )
            with c2:
                e_color = st.text_input("Primary color", value=w["primary_color"], key=f"c_{wid}")
            e_greeting = st.text_area(
                "Greeting text",
                value=w["greeting_text"],
                height=80,
                key=f"g_{wid}",
            )
            e_tools = st.multiselect(
                "Enabled tools",
                list(DEFAULT_TOOLS),
                default=w["enabled_tools"],
                key=f"t_{wid}",
            )
            save = st.form_submit_button("Save changes")
        if save:
            patch: dict[str, Any] = {}
            if e_name != w["name"]:
                patch["name"] = e_name
            origins_now = _csv_to_list(e_origins)
            if origins_now != w["allowed_origins"]:
                patch["allowed_origins"] = origins_now
            if e_position != w["position"]:
                patch["position"] = e_position
            if e_color != w["primary_color"]:
                patch["primary_color"] = e_color
            if e_greeting != w["greeting_text"]:
                patch["greeting_text"] = e_greeting
            if e_tools != w["enabled_tools"]:
                patch["enabled_tools"] = e_tools
            if not patch:
                st.info("No changes.")
            else:
                r = _api("PATCH", f"/api/admin/widgets/{wid}", json=patch)
                if r.status_code == 200:
                    st.success("Saved.")
                    st.rerun()
                else:
                    st.error(f"Patch failed: {r.status_code} {r.text[:300]}")

        # --- Delete row outside the form so it has its own confirm flow ----
        delete_key = f"confirm_delete_{wid}"
        if st.session_state.get(delete_key):
            colA, colB = st.columns(2)
            with colA:
                if st.button("Confirm delete", key=f"conf_{wid}", type="primary"):
                    r = _api("DELETE", f"/api/admin/widgets/{wid}")
                    if r.status_code == 204:
                        st.session_state[delete_key] = False
                        st.success(f"Deleted `{wid}`.")
                        st.rerun()
                    else:
                        st.error(f"Delete failed: {r.status_code} {r.text[:200]}")
            with colB:
                if st.button("Cancel", key=f"can_{wid}"):
                    st.session_state[delete_key] = False
                    st.rerun()
        else:
            if st.button("Delete widget", key=f"del_{wid}"):
                st.session_state[delete_key] = True
                st.rerun()
