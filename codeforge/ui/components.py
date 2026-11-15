"""Small reusable Streamlit pieces: page header and role-aware navigation."""
import streamlit as st

from codeforge.config import current_identity


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="cf-title">{title}</div><div class="cf-sub">{subtitle}</div>',
                unsafe_allow_html=True)


def table(rows: list[list[str]], headers: list[str]) -> None:
    import pandas as pd

    st.dataframe(pd.DataFrame(rows, columns=headers), use_container_width=True, hide_index=True)


def top_bar(pages: list[st.Page]) -> None:
    """One lane: brand | nav links | server-selected identity."""
    ident = current_identity()
    cols = st.columns([1.6, *[1.2] * len(pages), 2.0], vertical_alignment="center")
    cols[0].markdown('<div class="cf-brand">🔨 Code<span>Forge</span></div>', unsafe_allow_html=True)
    for col, page in zip(cols[1:-1], pages, strict=True):
        with col:
            st.page_link(page)
    cols[-1].caption(f"👤 {ident.user_id} · {ident.role}")
