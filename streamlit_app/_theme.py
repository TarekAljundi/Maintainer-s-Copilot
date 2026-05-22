"""Shared Streamlit visual theme for the admin console.

Native theming (`.streamlit/config.toml`) covers the colour palette + base
font. This module adds what config can't express: the Inter webfont and a thin
layer of CSS polish (heading weight, control radius, hover feel) so the console
matches the embeddable widget and the demo host page.

Call `apply()` once per page, immediately after `st.set_page_config()`.
"""

from __future__ import annotations

import streamlit as st

_CSS = """\
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

.stApp, .stApp button, .stApp input, .stApp textarea, .stApp select {
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}

/* Headings — tighter tracking, firmer weight */
.stApp h1 { font-weight: 700; letter-spacing: -.021em; }
.stApp h2 { font-weight: 600; letter-spacing: -.014em; }
.stApp h3 { font-weight: 600; letter-spacing: -.008em; }

/* Buttons — softer radius, calm hover, tactile press */
.stButton > button,
.stFormSubmitButton > button,
.stDownloadButton > button {
  border-radius: 8px;
  font-weight: 500;
  transition: filter .12s ease, transform .08s ease;
}
.stButton > button:hover,
.stFormSubmitButton > button:hover,
.stDownloadButton > button:hover { filter: brightness(1.05); }
.stButton > button:active,
.stFormSubmitButton > button:active,
.stDownloadButton > button:active { transform: translateY(1px); }

/* Inputs — match the 8px control radius */
.stTextInput input,
.stTextArea textarea,
.stNumberInput input,
.stSelectbox div[data-baseweb="select"] > div {
  border-radius: 8px;
}

@media (prefers-reduced-motion: reduce) {
  .stButton > button,
  .stFormSubmitButton > button,
  .stDownloadButton > button { transition: none; }
}
</style>
"""


def apply() -> None:
    """Inject the shared Inter webfont + CSS polish for the current page."""
    st.markdown(_CSS, unsafe_allow_html=True)
