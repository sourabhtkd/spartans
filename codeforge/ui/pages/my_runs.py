import streamlit as st

from codeforge.ui import handlers
from codeforge.ui.components import page_header, table

HEADERS = ["Run ID", "Owner", "Request", "Passed", "Failed", "Exit Code", "Created At"]


def render() -> None:
    page_header("My Runs", "History of your generation runs.")
    st.button("Refresh")
    table(handlers.my_runs_table(), HEADERS)
