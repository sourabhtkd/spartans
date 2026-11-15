"""Small reusable Streamlit pieces: page header, top bar with nav + profile dropdown."""
import streamlit as st

from codeforge.config import current_identity, get_settings
from codeforge.ui import handlers


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="cf-title">{title}</div><div class="cf-sub">{subtitle}</div>',
                unsafe_allow_html=True)


def table(rows: list[list[str]], headers: list[str]) -> None:
    import pandas as pd

    st.dataframe(pd.DataFrame(rows, columns=headers), use_container_width=True, hide_index=True)


def _initials(user_id: str) -> str:
    return user_id.replace("u_", "")[:2].upper()


def top_bar(pages: list[st.Page]) -> None:
    """One lane: brand | nav links | profile dropdown."""
    settings = get_settings()
    ident = current_identity()
    cols = st.columns([1.6, *[1.2] * len(pages), 0.4, 2.0], vertical_alignment="center")
    cols[0].markdown('<div class="cf-brand">🔨 Code<span>Forge</span></div>', unsafe_allow_html=True)
    for col, page in zip(cols[1:-2], pages, strict=True):
        with col:
            st.page_link(page)
    with cols[-1].popover(f"👤 {ident.user_id.replace('u_', '').title()} · {ident.role}",
                          use_container_width=True):
        st.markdown(f'**Signed in as** `{ident.user_id}`<br>'
                    f'<span class="cf-chip {ident.role}">{ident.role}</span>', unsafe_allow_html=True)
        names = list(settings.profiles)
        choice = st.radio("Switch user profile", names, index=names.index(settings.active_profile),
                          format_func=lambda n: f"{_initials(settings.profiles[n].user_id)} · {n} "
                                                f"({settings.profiles[n].role})")
        if choice != settings.active_profile:
            handlers.switch_profile(choice)
            st.rerun()
