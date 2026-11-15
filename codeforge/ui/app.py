"""Streamlit entrypoint: top navigation bar with profile switcher; logic lives in handlers.py."""
import streamlit as st

st.set_page_config(page_title="CodeForge — Multi-Agent NL to Code", page_icon="🔨", layout="wide")

from codeforge.config import current_identity
from codeforge.ui import components, theme
from codeforge.ui.pages import chat, my_runs, ops, reviewer, tickets


def build_pages() -> list[st.Page]:
    pages = [
        st.Page(chat.render, title="Chat", icon="💬", url_path="chat", default=True),
        st.Page(my_runs.render, title="My Runs", icon="📜", url_path="runs"),
    ]
    if current_identity().role == "instructor":
        pages.append(st.Page(reviewer.render, title="Reviewer Queue", icon="🛡️", url_path="review"))
    if current_identity().role != "student":
        pages.append(st.Page(tickets.render, title="Tickets", icon="🎫", url_path="tickets"))
    pages.append(st.Page(ops.render, title="Ops", icon="⚙️", url_path="ops"))
    return pages


st.markdown(theme.CSS, unsafe_allow_html=True)
_pages = build_pages()
_nav = st.navigation(_pages, position="hidden")
components.top_bar(_pages)
_nav.run()
