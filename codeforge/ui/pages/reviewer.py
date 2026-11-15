import streamlit as st

from codeforge.ui import handlers
from codeforge.ui.components import page_header, table

HEADERS = ["Thread ID", "Requester", "Policy Reasons", "Code Hash", "Created At"]


def render() -> None:
    page_header("Reviewer Queue", "Approve or reject runs paused by policy.")
    st.button("Refresh")
    table(handlers.pending_approvals_table(), HEADERS)
    st.markdown("##### Decision")
    tid = st.text_input("Thread ID")
    comment = st.text_input("Reviewer comment")
    ok, no, _ = st.columns([1, 1, 6])
    if ok.button("Approve", type="primary"):
        st.info(handlers.review_decision(tid, "Approve", comment))
    if no.button("Reject"):
        st.info(handlers.review_decision(tid, "Reject", comment))
