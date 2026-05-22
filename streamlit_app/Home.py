"""Streamlit landing page. Admin sign-in only.

PRD §Authentication §Streamlit. This is the admin console — only `role=admin`
accounts may sign in. Regular users register and sign in on the public host
page, not here, so registration deliberately does not exist on this surface.
JWT goes into st.session_state["jwt"] on successful admin login; every API
call from the other pages attaches it as `Authorization: Bearer …`.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

import _theme

API_BASE = os.environ.get("API_BASE", "http://api:8000")

st.set_page_config(page_title="Maintainer's Copilot")
_theme.apply()
st.title("Maintainer's Copilot")
st.caption("Admin console")

if "jwt" not in st.session_state:
    st.session_state["jwt"] = None
if "me" not in st.session_state:
    st.session_state["me"] = None


def _fetch_me(token: str) -> dict | None:
    try:
        r = httpx.get(
            f"{API_BASE}/api/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except httpx.HTTPError:
        pass
    return None


me = st.session_state["me"]

if me:
    st.success(f"Signed in as **{me.get('email', '?')}** (role: {me.get('role', '?')})")
    if st.button("Sign out"):
        try:
            httpx.post(
                f"{API_BASE}/auth/jwt/revoke",
                headers={"Authorization": f"Bearer {st.session_state['jwt']}"},
                timeout=5,
            )
        except httpx.HTTPError:
            pass
        st.session_state["jwt"] = None
        st.session_state["me"] = None
        st.rerun()
    st.markdown("Use the sidebar to open **Chat**, **Memory Inspector**, or **Widgets**.")
else:
    st.markdown(
        "Sign in with an **admin** account. New users register on the host page, "
        "not here — this console is admin-only."
    )
    with st.form("login_form"):
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in")
    if ok:
        try:
            r = httpx.post(
                f"{API_BASE}/auth/jwt/login",
                data={"username": email, "password": pw},
                timeout=15,
            )
        except httpx.HTTPError as exc:
            st.error(f"Network error: {exc}")
        else:
            if r.status_code != 200:
                st.error(f"Login failed: {r.status_code} {r.text[:200]}")
            else:
                token = r.json()["access_token"]
                profile = _fetch_me(token)
                if profile is None:
                    st.error("Signed in, but could not load your profile. Try again.")
                elif profile.get("role") != "admin":
                    # Admin-only console: never store a non-admin token. The
                    # token is left to expire on its own — it is never persisted.
                    st.error(
                        "This console is admin-only — your account is not an admin. "
                        "Regular users chat through the widget on the host page."
                    )
                else:
                    st.session_state["jwt"] = token
                    st.session_state["me"] = profile
                    st.rerun()
