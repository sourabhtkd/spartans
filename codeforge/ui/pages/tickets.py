import streamlit as st

from codeforge.config import current_identity
from codeforge.ui import handlers
from codeforge.ui.components import page_header, table

HEADERS = ["Ticket ID", "Priority", "SLA (hrs)", "Masked Request", "Reason", "Created At"]


def render() -> None:
    if current_identity().role != "instructor":
        st.error("Only instructors may view tickets.")
        return
    page_header("Tickets", "Escalations raised when automation could not finish a request.")
    st.button("Refresh")
    table(handlers.tickets_table(), HEADERS)
