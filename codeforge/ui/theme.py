"""Palette and CSS for the Streamlit app (professional navy / blue / teal)."""

NAVY = "#0F2A47"
BLUE = "#2563EB"
TEAL = "#0EA5A4"
SURFACE = "#F4F6FA"
BORDER = "#E2E8F0"

CSS = f"""
<style>
#MainMenu, footer, header[data-testid="stHeader"] {{ visibility: hidden; height: 0; }}
.block-container {{ padding-top: 0 !important; max-width: 1280px; }}
.stApp {{ background: {SURFACE}; }}

/* top bar: first horizontal block on the page */
div[data-testid="stMainBlockContainer"] > div > div[data-testid="stVerticalBlock"] > div:first-child
  div[data-testid="stHorizontalBlock"]:first-of-type {{
  background: {NAVY}; margin: 0 -1.5rem 1.5rem -1.5rem; padding: .55rem 1.5rem;
  align-items: center; border-radius: 0 0 14px 14px; box-shadow: 0 4px 14px rgba(15,42,71,.25);
}}
.cf-brand {{ color: #fff; font-weight: 700; font-size: 1.25rem; letter-spacing: .2px; white-space: nowrap; }}
.cf-brand span {{ color: {TEAL}; }}

/* nav links */
a[data-testid="stPageLink-NavLink"] {{
  color: #CBD5E1 !important; border-radius: 8px; padding: .4rem .8rem; justify-content: center;
  transition: background .15s;
}}
a[data-testid="stPageLink-NavLink"] p {{ color: inherit !important; font-weight: 500; white-space: nowrap; }}
a[data-testid="stPageLink-NavLink"]:hover {{ background: rgba(255,255,255,.10); color: #fff !important; }}
a[data-testid="stPageLink-NavLink"][aria-current="page"] {{
  background: {BLUE}; color: #fff !important; box-shadow: 0 2px 8px rgba(37,99,235,.45);
}}

/* profile popover trigger = pill dropdown */
div[data-testid="stPopover"] > button {{
  background: rgba(255,255,255,.08); color: #fff; border: 1px solid rgba(255,255,255,.25);
  border-radius: 999px; padding: .3rem .9rem; width: 100%;
}}
div[data-testid="stPopover"] > button:hover {{ background: rgba(255,255,255,.18); border-color: {TEAL}; }}
div[data-testid="stPopover"] > button p {{ color: #fff; font-weight: 600; }}

/* cards & controls */
.cf-card {{ background: #fff; border: 1px solid {BORDER}; border-radius: 12px; padding: 1rem 1.25rem;
  box-shadow: 0 1px 3px rgba(15,23,42,.06); margin-bottom: 1rem; }}
.cf-title {{ font-size: 1.5rem; font-weight: 700; color: {NAVY}; margin: 0 0 .25rem; }}
.cf-sub {{ color: #64748B; margin-bottom: 1rem; }}
.cf-chip {{ display:inline-block; padding: .1rem .6rem; border-radius: 999px; font-size: .75rem;
  font-weight: 600; background: {TEAL}22; color: {TEAL}; border: 1px solid {TEAL}55; }}
.cf-chip.instructor {{ background: {BLUE}22; color: {BLUE}; border-color: {BLUE}55; }}
div.stButton > button {{ border-radius: 8px; font-weight: 600; }}
div.stButton > button[kind="primary"] {{ background: {BLUE}; border: none; }}
div.stButton > button[kind="primary"]:hover {{ background: #1D4ED8; }}
div[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 10px; overflow: hidden; }}
</style>
"""
