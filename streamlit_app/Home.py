"""Streamlit landing page. Login + register forms.

PRD §Authentication §Streamlit. JWT goes into st.session_state["jwt"] on
successful login; every API call from the other pages attaches it as
`Authorization: Bearer …`.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://api:8000")

st.set_page_config(page_title="Maintainer's Copilot")
st.title("Maintainer's Copilot")

if "jwt" not in st.session_state:
    st.session_state["jwt"] = None
if "me" not in st.session_state:
    st.session_state["me"] = None


def _fetch_me() -> dict | None:
    if not st.session_state["jwt"]:
        return None
    try:
        r = httpx.get(
            f"{API_BASE}/api/me",
            headers={"Authorization": f"Bearer {st.session_state['jwt']}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except httpx.HTTPError:
        pass
    return None


if st.session_state["jwt"] and not st.session_state["me"]:
    st.session_state["me"] = _fetch_me()


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
    tab_login, tab_register = st.tabs(["Sign in", "Register"])

    with tab_login:
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
                if r.status_code == 200:
                    st.session_state["jwt"] = r.json()["access_token"]
                    st.session_state["me"] = None
                    st.rerun()
                else:
                    st.error(f"Login failed: {r.status_code} {r.text[:200]}")
            except httpx.HTTPError as exc:
                st.error(f"Network error: {exc}")

    with tab_register:
        with st.form("register_form"):
            email_r = st.text_input("Email", key="reg_email")
            pw_r = st.text_input("Password", type="password", key="reg_pw")
            ok_r = st.form_submit_button("Create account")
        if ok_r:
            try:
                r = httpx.post(
                    f"{API_BASE}/auth/register",
                    json={"email": email_r, "password": pw_r},
                    timeout=15,
                )
                if r.status_code in (200, 201):
                    st.success("Account created. Sign in on the left tab.")
                else:
                    st.error(f"Registration failed: {r.status_code} {r.text[:200]}")
            except httpx.HTTPError as exc:
                st.error(f"Network error: {exc}")
