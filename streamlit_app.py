"""Vägvis 2.3.0: tre filer i GitHub, färdiga indata, inga arbetsflöden.

Start lokalt: python -m streamlit run streamlit_app.py
Filerna requirements.txt och vagvis_data.json ska ligga bredvid denna fil.
Beräkningarna körs i det ursprungliga Vägvis-gränssnittet i webbläsaren.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().with_name("vagvis_data.json")
PAGES = {
    "vagvis": "kallkod/source/shell.html",
    "metod": "app/Metod_och_anvandning.html",
    "trafikdata": "app/Trafikdata_till_Malmo.html",
}
MARKER = re.compile(r"\{\{([^}]+)\}\}")


@lru_cache(maxsize=1)
def _bundle(path: str, modified_ns: int, size: int) -> dict:
    del modified_ns, size  # Ingår i cachenyckeln när datafilen uppdateras.
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(bundle, dict) or bundle.get("schema") != "vagvis-flat-bundle-1":
        raise ValueError("Datafilen har inte Vägvis-paketets förväntade format.")
    files = bundle.get("files")
    if not isinstance(files, dict) or any(not isinstance(v, str) for v in files.values()):
        raise ValueError("Datafilens modell- och sidinnehåll är ogiltigt.")
    return files


def _embed(name: str, text: str) -> str:
    text = text.strip()
    if name.endswith(".json"):
        json.loads(text)
        return text.replace("<", "\\u003c")
    if name.endswith(".js"):
        return re.sub(r"</script", r"<\\/script", text, flags=re.IGNORECASE)
    return text


def page_html(name: str = "vagvis") -> str:
    """Läs sidor och indata ur en enda lokal fil, utan nätverk eller extrahering."""
    if name not in PAGES:
        raise ValueError("Okänd sida")
    stat = DATA.stat()
    files = _bundle(str(DATA), stat.st_mtime_ns, stat.st_size)
    html = files[PAGES[name]]
    if name == "vagvis":
        html = MARKER.sub(lambda match: _embed(match[1], files[match[1]]), html)
        if MARKER.search(html):
            raise ValueError("Datafilen saknar innehåll som modellen behöver.")
    return html.replace("Metod_och_anvandning.html", "/metod").replace(
        "Trafikdata_till_Malmo.html", "/trafikdata"
    )


def main() -> None:
    import streamlit as st
    import streamlit.components.v1 as components

    st.set_page_config(page_title="Vägvis 2.3.0", page_icon="🚦", layout="wide", initial_sidebar_state="collapsed")
    st.markdown("""<style>
[data-testid="stHeader"], [data-testid="stSidebar"], footer {display:none;}
.block-container {padding:0 !important;max-width:100% !important;}
[data-testid="stMainBlockContainer"] {padding:0 !important;}
iframe {border:0;}
</style>""", unsafe_allow_html=True)

    def show(name: str) -> None:
        try:
            html = page_html(name)
        except (OSError, ValueError, KeyError) as error:
            st.error("Vägvis kunde inte läsa vagvis_data.json. Ladda upp datafilen bredvid streamlit_app.py och försök igen.")
            st.caption(str(error))
            return
        components.html(html, height=1050, scrolling=True)

    pages = [
        st.Page(lambda: show("vagvis"), title="Vägvis", url_path="vagvis", default=True),
        st.Page(lambda: show("metod"), title="Metod och källor", url_path="metod"),
        st.Page(lambda: show("trafikdata"), title="Trafikdata", url_path="trafikdata"),
    ]
    st.navigation(pages, position="hidden").run()


if __name__ == "__main__":
    main()
