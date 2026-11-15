import streamlit as st

from codeforge.config import get_settings
from codeforge.ui import handlers
from codeforge.ui.components import page_header


def render() -> None:
    page_header("Ops & Telemetry", "Per-agent cost, latency, circuit breakers and chaos testing.")
    st.button("Refresh")
    st.markdown(f'<div class="cf-card">{handlers.ops_summary()}</div>', unsafe_allow_html=True)
    rate = st.slider("Chaos failure rate (instructor only)", 0.0, 1.0, float(get_settings().chaos_failure_rate),
                     step=0.1)
    if rate != float(get_settings().chaos_failure_rate):
        st.info(handlers.set_chaos_rate(rate))
