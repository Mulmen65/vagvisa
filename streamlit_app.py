"""Vägvis 2.3.1: tre filer i GitHub, färdiga indata, inga arbetsflöden.

Start lokalt: python -m streamlit run streamlit_app.py
Filerna requirements.txt och vagvis_data.json ska ligga bredvid denna fil.
Beräkningarna körs i det ursprungliga Vägvis-gränssnittet i webbläsaren.
"""
from __future__ import annotations

import json
import re
import urllib.parse
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


def page_html(name: str = "vagvis", files_override: dict | None = None) -> str:
    """Läs sidor och indata ur en enda lokal fil, utan nätverk eller extrahering."""
    if name not in PAGES:
        raise ValueError("Okänd sida")
    if files_override is None:
        stat = DATA.stat()
        files = _bundle(str(DATA), stat.st_mtime_ns, stat.st_size)
    else:
        files = files_override
    html = files[PAGES[name]]
    if name == "vagvis":
        html = MARKER.sub(lambda match: _embed(match[1], files[match[1]]), html)
        if MARKER.search(html):
            raise ValueError("Datafilen saknar innehåll som modellen behöver.")
    return html.replace("Metod_och_anvandning.html", "/metod").replace(
        "Trafikdata_till_Malmo.html", "/trafikdata"
    )


def indicator_page(st, back_page) -> None:
    """Valbar fil- och källhantering; modellerna ändras bara efter validering."""
    with st.container(border=True):
        st.title("Komplettera körsträckeindikatorer")
        st.write("Hämta inkomst, dagligvarubutiker och större kollektivtrafiknoder direkt till modellen. Inga API-nycklar behövs för dessa källor.")
        st.page_link(back_page, label="Tillbaka till Vägvis", icon="⬅️")
        try:
            base = st.session_state.get("vagvis_updated_bundle")
            if base is None:
                base = json.loads(DATA.read_text(encoding="utf-8"))
            if not isinstance(base, dict) or base.get("schema") != "vagvis-flat-bundle-1":
                raise ValueError("Datafilen har fel format.")
            current = json.loads(base["files"]["kallkod/data/distance-indicators.json"])
            if not isinstance(current, dict) or not isinstance(current.get("rows"), list):
                raise ValueError("Datafilens indikatorunderlag har fel format.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            st.error("Vägvis kunde inte läsa vagvis_data.json. Ladda upp datafilen bredvid streamlit_app.py och försök igen.")
            st.caption(str(error))
            return
        _indicator_coverage(st, current)
        st.caption("Inkomst: median ekonomisk standard 2024. Service: dagligvarubutiker. Kollektivtrafik: avstånd till fysisk större nod; turtäthet och restid ingår inte. OSM beskriver läget vid hämtningen och överförs öppet till beräkningsåret 2024.")

        if st.button("Hämta indikatorer", type="primary"):
            with st.status("Hämtar och kontrollerar källorna…", expanded=True) as status:
                try:
                    result, receipt, raw = indicator_fetch(base["files"], on_progress=status.write)
                    _indicator_activate(st, base, result, receipt, raw)
                    succeeded = any(source.get("success") for source in receipt.get("sources", []))
                    status.update(label="Hämtningen är klar" if succeeded else "Ingen ny källa kunde hämtas", state="complete" if succeeded else "error")
                except (ValueError, OSError, KeyError) as error:
                    status.update(label="Underlaget kunde inte uppdateras", state="error")
                    st.error(str(error))

        with st.expander("Reservväg: läs in nedladdade källfiler"):
            st.write("Om en källa inte går att hämta från servern kan du ladda ner den i webbläsaren och välja filen här. Inkomst kan läsas in separat. OSM-filerna behöver omfatta alla tre kommunernas sökrutor. Både Overpass JSON och Overpass Turbo GeoJSON med punkter stöds.")
            st.markdown("[Ladda ner SCB:s originalfil TAB6684_sv.zip](https://www.statistikdatabasen.scb.se/Resources/PX/bulk/ssd/sv/TAB6684_sv.zip). Behåll ZIP-formatet; appen väljer rätt år, mått och områden.")
            for code, name in (("1280", "Malmö"), ("1283", "Helsingborg"), ("2180", "Gävle")):
                url = "https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": indicator_access_query(code)})
                st.markdown(f"[Öppna OSM-uttag för {name}]({url}) och spara svaret som en JSON-fil i webbläsaren.")
            with st.form("vagvis_original_sources"):
                income_file = st.file_uploader("SCB: TAB6684_sv.zip", type=["zip"], key="income_original")
                osm_files = {code: st.file_uploader("OSM: " + name, type=["json"], key="osm_" + code) for code, name in (("1280", "Malmö"), ("1283", "Helsingborg"), ("2180", "Gävle"))}
                submitted = st.form_submit_button("Läs in källfiler")
            if submitted:
                try:
                    selected_osm = {code: file.getvalue() for code, file in osm_files.items() if file is not None}
                    if income_file is None and not selected_osm:
                        raise ValueError("Välj minst en källfil att läsa in.")
                    with st.spinner("Kontrollerar områden, källor och avstånd…"):
                        result, receipt, raw = indicator_import(base["files"], income_zip=income_file.getvalue() if income_file else None, osm_exports=selected_osm or None)
                        _indicator_activate(st, base, result, receipt, raw)
                except (ValueError, OSError, KeyError) as error:
                    st.error(str(error))

        updated = st.session_state.get("vagvis_updated_bundle")
        if updated is not None:
            st.success("Det kontrollerade underlaget används nu i denna Streamlit-session. Återgå till Vägvis och välj det valbara DeSO-läget för att använda indikatorerna i beräkningen.")
            _indicator_coverage(st, json.loads(updated["files"]["kallkod/data/distance-indicators.json"]))
            st.page_link(back_page, label="Använd underlaget i Vägvis", icon="🚦")
            st.write("För att behålla underlaget för alla användare: ladda ner datafilen nedan, ersätt vagvis_data.json i GitHub och gör Commit. Innan dess gäller uppdateringen bara din nuvarande server-session.")
            raw_bundle = json.dumps(updated, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            if len(raw_bundle) < 25 * 1024 * 1024:
                st.download_button("Ladda ner uppdaterad vagvis_data.json", data=raw_bundle, file_name="vagvis_data.json", mime="application/json")
            else:
                st.error("Den sammanställda filen överstiger GitHubs gräns för webbläsaruppladdning. Exportera indikatorfilen och behåll nuvarande datapaket.")
            indicator_bytes = updated["files"]["kallkod/data/distance-indicators.json"].encode("utf-8")
            st.download_button("Ladda ner enbart indikatorfil", data=indicator_bytes, file_name="vagvis_indikatorer.json", mime="application/json")
        receipt = st.session_state.get("vagvis_acquisition_receipt")
        if receipt:
            with st.expander("Källkvitto: utfall och hämtningstid"):
                st.json(receipt, expanded=False)
        with st.expander("Definitioner och källor"):
            st.markdown("**SCB (2026), TAB6684:** median ekonomisk standard per konsumtionsenhet, i tusentals kronor i 2024 års priser. Områdesstatistik för samtliga individer; inte bilägarnas särskilda inkomst eller individuell årslön. [Öppna tabellen](https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__HE__HE0110__HE0110I/Tab3InkDesoRegso/).")
            st.markdown("**OpenStreetMap-bidragsgivare (u.å.):** butiker med supermarket/convenience och kollektivtrafiknoder station/halt/tram_stop/bus_station/public_transport=station i dokumenterade sökrutor, även utanför kommungränsen. OSM-täckningen är inte verifierat fullständig. [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API), [licens ODbL](https://www.openstreetmap.org/copyright).")
            st.write("Avstånden beräknas från modellens befintliga 30 längdviktade startpunkter längs bostadsgator i varje DeSO. Fågelvägsavstånd är en geografisk proxy. Saknade eller sekretesskyddade värden lämnas saknade. Nya indikatorer innebär inte att sambandets koefficienter har skattats empiriskt.")


def _indicator_coverage(st, indicator: dict) -> None:
    names = {"centreKm": "Centrumavstånd", "income": "Median ekonomisk standard", "serviceKm": "Dagligvarubutiker", "transitKm": "Större kollektivtrafiknod", "bph": "Bilar per hushåll"}
    cities = {"1280": "Malmö", "1283": "Helsingborg", "2180": "Gävle"}
    rows = indicator.get("rows", [])
    table = []
    for key, label in names.items():
        row = {"Indikator": label}
        for city, name in cities.items():
            selected = [r for r in rows if str(r.get("kk")) == city]
            row[name] = str(sum(r.get(key) is not None for r in selected)) + " / " + str(len(selected))
        table.append(row)
    st.dataframe(table, hide_index=True, use_container_width=True)


def _indicator_activate(st, base: dict, indicator: dict, receipt: dict, raw: dict) -> None:
    st.session_state["vagvis_acquisition_receipt"] = receipt
    if any(source.get("success") for source in receipt.get("sources", [])):
        updated = indicator_updated_bundle(base, indicator, receipt, raw)
        st.session_state["vagvis_updated_bundle"] = updated
    else:
        st.error("Ingen ny källa kunde läsas in. Tidigare underlag behålls. Se källkvittot eller använd reservvägen med nedladdade filer.")


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
            updated = st.session_state.get("vagvis_updated_bundle")
            html = page_html(name, updated["files"] if updated else None)
        except (OSError, ValueError, KeyError) as error:
            st.error("Vägvis kunde inte läsa vagvis_data.json. Ladda upp datafilen bredvid streamlit_app.py och försök igen.")
            st.caption(str(error))
            return
        if name == "vagvis":
            st.page_link(pages[3], label="Hämta eller läs in körsträckeindikatorer", icon="📥")
        components.html(html, height=1050, scrolling=True)

    pages = [
        st.Page(lambda: show("vagvis"), title="Vägvis", url_path="vagvis", default=True),
        st.Page(lambda: show("metod"), title="Metod och källor", url_path="metod"),
        st.Page(lambda: show("trafikdata"), title="Trafikdata", url_path="trafikdata"),
        st.Page(lambda: indicator_page(st, pages[0]), title="Komplettera indikatorer", url_path="indikatorer"),
    ]
    st.navigation(pages, position="hidden").run()


# --- Källhämtning och validering: Python standardbibliotek ---
# Optional indicator acquisition. These functions use the Python standard library;
# network access happens only when indicator_fetch is called by an explicit action.
import copy as indicator_copy
import csv as indicator_csv
import hashlib as indicator_hashlib
import html as indicator_html
import io as indicator_io
import json as indicator_json
import math as indicator_math
import re as indicator_re
import urllib.parse as indicator_urlparse
import urllib.request as indicator_urlrequest
import zipfile as indicator_zipfile
from concurrent.futures import ThreadPoolExecutor as indicator_ThreadPoolExecutor
from datetime import datetime as indicator_datetime, timezone as indicator_timezone

INDICATOR_YEAR = 2024
INDICATOR_CITIES = ("1280", "1283", "2180")
INDICATOR_SCB_URL = "https://www.statistikdatabasen.scb.se/Resources/PX/bulk/ssd/sv/TAB6684_sv.zip"
INDICATOR_OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
INDICATOR_BBOX = {"1280": (55.35, 12.65, 55.85, 13.45),
                  "1283": (55.80, 12.40, 56.35, 13.40),
                  "2180": (60.25, 16.40, 61.25, 17.80)}
INDICATOR_FIELDS = ("centreKm", "transitKm", "serviceKm", "income", "bph")
INDICATOR_DATA_PATH = "kallkod/data/distance-indicators.json"
INDICATOR_ORIGINS_PATH = "underlag/korstracka/originpunkter.json"
INDICATOR_RECEIPT_PATH = "underlag/korstracka/senaste_hamtning.json"
INDICATOR_MAX_BYTES = 100_000_000


def indicator_timestamp():
    return indicator_datetime.now(indicator_timezone.utc).replace(microsecond=0).isoformat()


def indicator_digest(raw):
    return indicator_hashlib.sha256(raw).hexdigest()


def indicator_json_text(obj):
    return indicator_json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False) + "\n"


def indicator_number(value, positive=False):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and indicator_math.isfinite(value) and (value > 0 if positive else value >= 0))


def indicator_context(files):
    """Validate the existing geography and frozen origin samples, without changing them."""
    if not isinstance(files, dict):
        raise ValueError("Modellpaketets filer saknas.")
    try:
        households = indicator_json.loads(files["kallkod/data/household-project.json"])["d"]
        base = indicator_json.loads(files[INDICATOR_DATA_PATH])
        origin_doc = indicator_json.loads(files[INDICATOR_ORIGINS_PATH])
    except (KeyError, TypeError, indicator_json.JSONDecodeError) as error:
        raise ValueError("Paketet saknar giltiga hushållsdata, indikatorer eller startpunkter.") from error
    if not isinstance(households, list) or len(households) != 348:
        raise ValueError("Hushållsunderlaget ska innehålla exakt 348 DeSO.")
    stock = {}
    for row in households:
        if not isinstance(row, dict):
            raise ValueError("Ogiltig hushållsrad.")
        code, city = row.get("kod"), str(row.get("kk"))
        if (not isinstance(code, str) or not indicator_re.fullmatch(r"\d{4}[ABC]\d{4}", code)
                or city not in INDICATOR_CITIES or not code.startswith(city) or code in stock):
            raise ValueError("Okänd eller dubbel DeSO-kod i hushållsunderlaget.")
        stock[code] = row
    if (not isinstance(base, dict) or base.get("schema") != "vagvis-distance-indicators-1"
            or base.get("dataYear") != INDICATOR_YEAR or str(base.get("desoVersion")) != "2025"):
        raise ValueError("Indikatorerna ska avse år 2024 och DeSO 2025.")
    rows = base.get("rows")
    if not isinstance(rows, list) or len(rows) != len(stock):
        raise ValueError("Indikatorunderlaget ska innehålla samtliga 348 DeSO.")
    seen = set()
    for row in rows:
        code = row.get("kod") if isinstance(row, dict) else None
        if code not in stock or code in seen or str(row.get("kk")) != str(stock[code]["kk"]):
            raise ValueError("Indikatorernas DeSO-koder och kommuner stämmer inte med registret.")
        seen.add(code)
        for key in INDICATOR_FIELDS:
            if row.get(key) is not None and not indicator_number(row[key]):
                raise ValueError("Indikatorvärdet är ogiltigt: " + code + "/" + key)
    if (not isinstance(origin_doc, dict) or origin_doc.get("schema") != "vagvis-origin-samples-1"
            or not isinstance(origin_doc.get("rows"), dict) or set(origin_doc["rows"]) != set(stock)):
        raise ValueError("Startpunkterna ska täcka exakt samma 348 DeSO.")
    for code, sample in origin_doc["rows"].items():
        if not isinstance(sample, dict) or not isinstance(sample.get("points"), list):
            raise ValueError("Ogiltiga startpunkter för " + code)
        for point in sample["points"]:
            if not isinstance(point, dict):
                raise ValueError("Ogiltig startpunkt för " + code)
            indicator_coordinates(point.get("lon"), point.get("lat"))
            if not indicator_number(point.get("weightKm"), positive=True):
                raise ValueError("Startpunktens vikt måste vara positiv: " + code)
    return base, stock, origin_doc["rows"]


def indicator_coordinates(lon, lat, city=None):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not indicator_math.isfinite(v)
           for v in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("Ogiltiga WGS84-koordinater.")
    if city is not None:
        if city not in INDICATOR_BBOX:
            raise ValueError("Okänd kommun för geografiskt underlag.")
        south, west, north, east = INDICATOR_BBOX[city]
        if not (west - 1e-9 <= lon <= east + 1e-9 and south - 1e-9 <= lat <= north + 1e-9):
            raise ValueError("OSM-punkt ligger utanför kommunens dokumenterade sökruta: " + city)
    return lon, lat


def indicator_validate_income(obj, expected):
    if (not isinstance(obj, dict) or obj.get("schema") != "vagvis-income-1"
            or obj.get("dataYear") != INDICATOR_YEAR or str(obj.get("desoVersion")) != "2025"
            or obj.get("measure") != "median_ekonomisk_standard"
            or obj.get("unit") != "tkr_2024_prices"):
        raise ValueError("Inkomst ska vara median ekonomisk standard, tkr i 2024 års priser, DeSO 2025.")
    if not isinstance(obj.get("rows"), list):
        raise ValueError("Inkomstfilen saknar rader.")
    values = {}
    for row in obj["rows"]:
        code = row.get("kod") if isinstance(row, dict) else None
        if code not in expected or code in values:
            raise ValueError("Okänd eller dubbel inkomstkod: " + str(code))
        value = row.get("income")
        if value is not None and not indicator_number(value, positive=True):
            raise ValueError("Inkomsten ska vara positiv eller saknad: " + code)
        values[code] = value
    if set(values) != set(expected):
        raise ValueError("Inkomstfilen ska täcka alla 348 DeSO, med null för skyddade celler.")
    return values


def indicator_fieldkey(text):
    return indicator_re.sub(r"[^a-zåäö0-9]", "", str(text).lower())


def indicator_parse_income_zip(raw, expected):
    """Read SCB TAB6684 wide/long CSV, selecting median tkr and 2024 only."""
    if not isinstance(raw, bytes) or len(raw) > INDICATOR_MAX_BYTES:
        raise ValueError("SCB-filen ska vara en ZIP-fil under 100 MB.")
    try:
        with indicator_zipfile.ZipFile(indicator_io.BytesIO(raw)) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1 or archive.getinfo(members[0]).file_size > 250_000_000:
                raise ValueError("SCB-exporten ska innehålla exakt en CSV-fil under 250 MB.")
            member, csv_raw = members[0], archive.read(members[0])
    except (indicator_zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError("SCB-filen kunde inte öppnas som ZIP.") from error
    text, encoding = None, None
    for candidate in ("utf-8-sig", "cp1252"):
        try:
            text, encoding = csv_raw.decode(candidate), candidate
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("SCB-exportens textkodning kunde inte läsas.")
    try:
        delimiter = indicator_csv.Sniffer().sniff(text[:20000], delimiters=",;\t").delimiter
    except indicator_csv.Error as error:
        raise ValueError("SCB-exportens CSV-avgränsare kunde inte identifieras.") from error
    reader = indicator_csv.DictReader(indicator_io.StringIO(text), delimiter=delimiter)
    fields = reader.fieldnames or []
    normal = {field: indicator_fieldkey(field) for field in fields}
    regions = [f for f, key in normal.items() if key in ("region", "deso", "desokod")]
    years = [f for f, key in normal.items() if key in ("år", "ar", "tid", "year")]
    contents = [f for f, key in normal.items() if key in ("tabellinnehåll", "tabellinnehall", "contentscode", "innehåll", "innehall")]
    medians = [f for f, key in normal.items() if "median" in key and ("tkr" in key or "tusentalskronor" in key)]
    if len(regions) != 1 or len(years) != 1:
        raise ValueError("SCB-exporten saknar entydiga kolumner för region och år.")
    if len(medians) == 1:
        layout, value_field = "wide_median_column", medians[0]
    elif not medians and len(contents) == 1:
        layout = "long_content_dimension"
        candidates = [f for f in fields if f not in regions + years + contents]
        if len(candidates) != 1:
            raise ValueError("SCB-exportens värdekolumn är tvetydig.")
        value_field = candidates[0]
    else:
        raise ValueError("SCB-exporten saknar ett entydigt medianvärde i tkr.")
    header_price_years = indicator_re.findall(r"(20\d{2})\s*års?\s*pris", value_field.lower())
    if header_price_years and any(year != "2024" for year in header_price_years):
        raise ValueError("SCB-exportens prisår ska vara 2024.")
    selected, count = {}, 0
    duplicate_resolution = {"repeatedRows": 0, "missingWithObserved": 0, "identicalObserved": 0, "repeatedMissing": 0}
    for row in reader:
        count += 1
        if str(row.get(years[0], "")).strip() != str(INDICATOR_YEAR):
            continue
        if layout == "long_content_dimension":
            measure = indicator_fieldkey(row.get(contents[0], ""))
            if not ("median" in measure and ("tkr" in measure or "tusentalskronor" in measure)):
                continue
        region = str(row.get(regions[0], "")).strip()
        match = indicator_re.match(r"^(\d{4}[ABC]\d{4})(?:\s|$)", region)
        code = match[1] if match else None
        if code not in expected:
            continue
        if None in row:
            raise ValueError("SCB-raden innehåller fler värden än CSV-rubriken: " + code)
        value = str(row.get(value_field, "")).strip()
        if value in ("", "..", "...", ".", ":"):
            number = None
        else:
            try:
                number = float(value.replace("\xa0", "").replace(" ", "").replace(",", "."))
            except ValueError as error:
                raise ValueError("Ogiltig inkomstcell för " + code) from error
        if number is not None and not indicator_number(number, positive=True):
            raise ValueError("Inkomst ska vara positiv eller saknad: " + code)
        if code in selected:
            duplicate_resolution["repeatedRows"] += 1
            previous = selected[code]
            if previous is None and number is None:
                duplicate_resolution["repeatedMissing"] += 1
            elif previous is None or number is None:
                duplicate_resolution["missingWithObserved"] += 1
                selected[code] = number if previous is None else previous
            elif previous == number:
                duplicate_resolution["identicalObserved"] += 1
            else:
                raise ValueError("Dubbelt medianvärde med olika observationer för " + code + ": " + str(previous) + " och " + str(number))
        else:
            selected[code] = number
    obj = {"schema": "vagvis-income-1", "dataYear": INDICATOR_YEAR, "desoVersion": "2025",
           "measure": "median_ekonomisk_standard", "unit": "tkr_2024_prices",
           "rows": [{"kod": code, "income": value} for code, value in sorted(selected.items())],
           "source": {"url": INDICATOR_SCB_URL, "table": "TAB6684", "zipSha256": indicator_digest(raw),
                      "zipBytes": len(raw), "csvSha256": indicator_digest(csv_raw), "member": member,
                      "encoding": encoding, "delimiter": delimiter, "parsedRows": count, "layout": layout,
                      "filter": {"year": INDICATOR_YEAR, "content": "Medianvärde, tkr", "desoVersion": "2025"},
                      "duplicateResolution": {**duplicate_resolution, "rule": "For the same code/year/median, keep the sole observed value when other rows are missing; identical observations are collapsed. Different observed values fail explicitly. Missing values are never imputed."}}}
    indicator_validate_income(obj, expected)
    return obj


def indicator_access_query(city):
    if city not in INDICATOR_BBOX:
        raise ValueError("Okänd kommun.")
    box = ",".join(str(x) for x in INDICATOR_BBOX[city])
    return ('[out:json][timeout:25];('
            'nwr["shop"~"^(supermarket|convenience)$"](' + box + ');'
            'nwr["railway"~"^(station|halt|tram_stop)$"](' + box + ');'
            'nwr["amenity"="bus_station"](' + box + ');'
            'nwr["public_transport"="station"](' + box + '););out center;')


def indicator_parse_osm(raw, city):
    if city not in INDICATOR_CITIES:
        raise ValueError("OSM-exporten avser en okänd kommun.")
    if not isinstance(raw, bytes) or len(raw) > INDICATOR_MAX_BYTES:
        raise ValueError("OSM-exporten ska vara en JSON-fil under 100 MB.")
    try:
        obj = indicator_json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError("OSM-exporten kunde inte läsas som JSON.") from error
    if not isinstance(obj, dict):
        raise ValueError("OSM-exporten ska vara ett JSON-objekt.")
    if obj.get("remark"):
        raise ValueError("Overpass rapporterar ett fel: " + str(obj["remark"]))
    if obj.get("type") == "FeatureCollection":
        if not isinstance(obj.get("features"), list):
            raise ValueError("GeoJSON-exporten saknar en lista med features.")
        elements = []
        for feature in obj["features"]:
            if not isinstance(feature, dict) or feature.get("type") != "Feature" or not isinstance(feature.get("properties"), dict):
                raise ValueError("GeoJSON-exporten innehåller ett ogiltigt objekt.")
            tags = feature["properties"]
            ident = feature.get("id", tags.get("@id"))
            if tags.get("@id") is not None and tags["@id"] != ident:
                raise ValueError("GeoJSON-objektets OSM-identiteter stämmer inte överens.")
            match = indicator_re.fullmatch(r"(node|way|relation)/([1-9]\d*)", ident or "") if isinstance(ident, str) else None
            if match is None:
                raise ValueError("GeoJSON-objektet saknar en entydig OSM-identitet, till exempel node/123.")
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("type") != "Point":
                raise ValueError("GeoJSON-exporten ska innehålla punkter från out center. Polygoner räknas inte om till en egen påhittad mittpunkt.")
            coordinates = geometry.get("coordinates")
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                raise ValueError("GeoJSON-punkten saknar WGS84-koordinater.")
            location = {"lon": coordinates[0], "lat": coordinates[1]}
            element = {"type": match[1], "id": int(match[2]), "tags": {k: v for k, v in tags.items() if not k.startswith("@")}}
            if match[1] == "node":
                element.update(location)
            else:
                element["center"] = location
            elements.append(element)
        stamp = obj.get("timestamp")
    elif isinstance(obj.get("elements"), list):
        elements = obj["elements"]
        stamp = obj.get("osm3s", {}).get("timestamp_osm_base") if isinstance(obj.get("osm3s", {}), dict) else None
    else:
        raise ValueError("OSM-exporten ska vara Overpass JSON med elements eller Overpass Turbo GeoJSON med FeatureCollection och punkter.")
    points, seen = [], set()
    for element in elements:
        if not isinstance(element, dict) or not isinstance(element.get("tags", {}), dict):
            raise ValueError("Ogiltigt OSM-element.")
        tags = element.get("tags") or {}
        kind = ("service" if tags.get("shop") in ("supermarket", "convenience") else
                "transit" if tags.get("railway") in ("station", "halt", "tram_stop") or
                tags.get("amenity") == "bus_station" or tags.get("public_transport") == "station" else None)
        if kind is None or any(tags.get(key) in ("yes", "true", "1") for key in ("disused", "abandoned", "proposed", "construction")):
            continue
        if element.get("type") not in ("node", "way", "relation") or not isinstance(element.get("id"), int) or isinstance(element["id"], bool):
            raise ValueError("OSM-punkt saknar giltig identitet.")
        location = element if element["type"] == "node" else element.get("center")
        if not isinstance(location, dict):
            raise ValueError("OSM-exporten saknar mittpunkt. Använd out center.")
        lon, lat = indicator_coordinates(location.get("lon"), location.get("lat"), city)
        ident = str(element["type"]) + "/" + str(element["id"])
        if (kind, ident) in seen:
            raise ValueError("Dubblett i OSM-exporten: " + ident)
        seen.add((kind, ident))
        points.append({"id": ident, "kk": city, "kind": kind, "lon": lon, "lat": lat,
                       "name": tags.get("name"),
                       "tags": {key: tags[key] for key in ("shop", "railway", "amenity", "public_transport") if key in tags}})
    if any(not any(point["kind"] == kind for point in points) for kind in ("service", "transit")):
        raise ValueError("OSM-exporten saknar dagligvaror eller större kollektivtrafiknoder för " + city)
    if stamp is not None and not isinstance(stamp, str):
        raise ValueError("OSM-exportens källdatum är ogiltigt.")
    return points, stamp


def indicator_osm_export_info(raw):
    obj = indicator_json.loads(raw)
    geojson = obj.get("type") == "FeatureCollection"
    return {"format": "Overpass Turbo GeoJSON" if geojson else "Overpass JSON",
            "timestampKind": "export_timestamp" if geojson else "osm_database_timestamp",
            "generator": obj.get("generator"), "copyright": obj.get("copyright")}


def indicator_validate_pois(obj):
    if not isinstance(obj, dict) or obj.get("schema") != "vagvis-osm-access-points-1" or not isinstance(obj.get("points"), list):
        raise ValueError("Fel format för validerade OSM-punkter.")
    grouped = {city: {"transit": [], "service": []} for city in INDICATOR_CITIES}
    seen = set()
    for point in obj["points"]:
        if not isinstance(point, dict):
            raise ValueError("Ogiltig OSM-punkt.")
        city, kind, ident = str(point.get("kk")), point.get("kind"), point.get("id")
        if city not in grouped or kind not in grouped[city] or not isinstance(ident, str) or not ident:
            raise ValueError("Okänd kommun, kategori eller identitet i OSM-punkterna.")
        if (city, kind, ident) in seen:
            raise ValueError("Dubbel OSM-punkt: " + ident)
        seen.add((city, kind, ident))
        grouped[city][kind].append(indicator_coordinates(point.get("lon"), point.get("lat"), city))
    if any(not grouped[city][kind] for city in INDICATOR_CITIES for kind in ("service", "transit")):
        raise ValueError("OSM-punkterna ska innehålla båda kategorierna för samtliga tre kommuner.")
    return grouped


def indicator_km(a, b):
    x1, y1, x2, y2 = map(indicator_math.radians, (a[0], a[1], b[0], b[1]))
    h = indicator_math.sin((y2-y1)/2)**2 + indicator_math.cos(y1)*indicator_math.cos(y2)*indicator_math.sin((x2-x1)/2)**2
    return 2*6371.0088*indicator_math.asin(min(1, indicator_math.sqrt(max(0, h))))


def indicator_weighted_dist(sample, destinations):
    if not sample["points"] or not destinations:
        return None
    total = sum(point["weightKm"] for point in sample["points"])
    return sum(point["weightKm"] * min(indicator_km((point["lon"], point["lat"]), destination)
                                      for destination in destinations)
               for point in sample["points"]) / total


def indicator_receipt(origin):
    return {"schema": "vagvis-distance-acquisition-receipt-1", "started": indicator_timestamp(),
            "targetYear": INDICATOR_YEAR, "desoVersion": "2025", "origin": origin, "sources": [],
            "rule": "Failed source preserves earlier values. Missing data remain null. No coefficients are fitted."}


def indicator_build(files, income=None, pois=None, receipt=None, raw_sources=None):
    base, stock, samples = indicator_context(files)
    result = indicator_copy.deepcopy(base)
    income_values = indicator_validate_income(income, stock) if income is not None else None
    poi_values = indicator_validate_pois(pois) if pois is not None else None
    for row in result["rows"]:
        code, city = row["kod"], str(row["kk"])
        quality = row.setdefault("quality", {})
        if income_values is not None:
            row["income"] = income_values[code]
            quality["income"] = "observed_area_statistic" if row["income"] is not None else "missing"
        if poi_values is not None:
            for kind, field in (("service", "serviceKm"), ("transit", "transitKm")):
                value = indicator_weighted_dist(samples[code], poi_values[city][kind])
                row[field] = round(value, 8) if value is not None else None
                quality[field] = "spatial_proxy_transferred_2026" if value is not None else "missing"
    meta = result.setdefault("metadata", {})
    meta["coverage"] = {field: {"available": sum(row.get(field) is not None for row in result["rows"]),
                                "total": len(result["rows"])} for field in INDICATOR_FIELDS}
    meta["coverageByCity"] = {city: {field: {"available": sum(row.get(field) is not None for row in result["rows"] if str(row["kk"]) == city),
                                               "total": sum(str(row["kk"]) == city for row in result["rows"])}
                                         for field in INDICATOR_FIELDS} for city in INDICATOR_CITIES}
    meta["status"] = ("observations_with_spatial_proxies" if all(value["available"] == value["total"] for value in meta["coverage"].values())
                       else "partial_observations_with_spatial_proxies")
    source_years = meta.setdefault("sourceYears", {})
    if income is not None:
        source_years["income"] = INDICATOR_YEAR
    if pois is not None:
        source_years["pois"] = pois.get("osmTimestamp")
        meta["poiTimestampKinds"] = sorted({s.get("timestampKind", "not_specified") for s in pois.get("sources", [])})
        if "export_timestamp" in meta["poiTimestampKinds"]:
            note = "GeoJSON-källans tidsstämpel är exportens tidsstämpel; den bevisar inte separat OSM-databasens datum eller att verksamheten fanns 2024."
            if note not in meta.setdefault("caveats", []):
                meta["caveats"].append(note)
    for source in meta.get("sources", []):
        if (source.get("id") == "SCB_TAB6684" and income is not None) or (source.get("id") == "OSM_ACCESS" and pois is not None):
            source["status"] = "cached_observations"
            source["retrieved"] = receipt.get("completed") if receipt else indicator_timestamp()
            if source.get("id") == "OSM_ACCESS":
                source["osmTimestamp"] = pois.get("osmTimestamp")
    if receipt is not None:
        meta["acquisitionReceipt"] = indicator_copy.deepcopy(receipt)
    inputs = {item["file"]: item for item in meta.get("inputFiles", []) if isinstance(item, dict) and item.get("file")}
    for name, text in {INDICATOR_ORIGINS_PATH: files[INDICATOR_ORIGINS_PATH], **(raw_sources or {})}.items():
        if name.endswith(".json"):
            raw = text.encode("utf-8")
            inputs[name] = {"file": name, "bytes": len(raw), "sha256": indicator_digest(raw)}
    meta["inputFiles"] = list(inputs.values())
    return result


def indicator_osm_document(city_results):
    if set(city_results) != set(INDICATOR_CITIES) or not all(city_results[city].get("success") for city in INDICATOR_CITIES):
        raise ValueError("Alla tre OSM-exporter måste vara fullständiga innan de aktiveras.")
    obj = {"schema": "vagvis-osm-access-points-1", "retrieved": indicator_timestamp(),
           "osmTimestamp": sorted({item["osmTimestamp"] for item in city_results.values() if item.get("osmTimestamp")}),
           "searchBboxes": INDICATOR_BBOX,
           "points": [point for city in INDICATOR_CITIES for point in city_results[city]["points"]],
           "sources": [{key: city_results[city].get(key) for key in ("city", "url", "query", "queryTemplate", "queryVerified", "sha256", "osmTimestamp", "format", "timestampKind", "generator", "copyright")}
                       for city in INDICATOR_CITIES]}
    indicator_validate_pois(obj)
    return obj


def indicator_import(files, income_zip=None, osm_exports=None, origin="manual", poi_json=None):
    """Validate all supplied sources before producing any new indicator or bundle."""
    _, stock, _ = indicator_context(files)
    if income_zip is None and osm_exports is None and poi_json is None:
        raise ValueError("Välj minst en inkomstfil eller en komplett uppsättning OSM-filer.")
    if osm_exports is not None and poi_json is not None:
        raise ValueError("Välj antingen originalexporter eller en samlad OSM-punktfil.")
    receipt, raw_sources = indicator_receipt(origin), {}
    income = indicator_parse_income_zip(income_zip, stock) if income_zip is not None else None
    pois = None
    if income is not None:
        raw_sources["underlag/korstracka/inkomst_2024.json"] = indicator_json_text(income)
        receipt["sources"].append({"source": "SCB_TAB6684", "success": True, "mode": origin,
                                   "url": INDICATOR_SCB_URL, "bytes": len(income_zip), "sha256": indicator_digest(income_zip)})
    if osm_exports is not None:
        if not isinstance(osm_exports, dict) or set(osm_exports) != set(INDICATOR_CITIES):
            raise ValueError("Ladda upp en Overpass-export för var och en av de tre kommunerna.")
        cities = {}
        for city in INDICATOR_CITIES:
            raw = osm_exports[city]
            points, stamp = indicator_parse_osm(raw, city)
            cities[city] = {"success": True, "city": city, "url": None, "query": None,
                            "queryTemplate": indicator_access_query(city), "queryVerified": False,
                            "points": points, "osmTimestamp": stamp, "sha256": indicator_digest(raw), **indicator_osm_export_info(raw)}
        pois = indicator_osm_document(cities)
    if poi_json is not None:
        try:
            pois = indicator_json.loads(poi_json) if isinstance(poi_json, (bytes, str)) else indicator_copy.deepcopy(poi_json)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Den samlade OSM-punktfilen kunde inte läsas.") from error
        indicator_validate_pois(pois)
    if pois is not None:
        text = indicator_json_text(pois)
        raw_sources["underlag/korstracka/osm_access_points.json"] = text
        receipt["sources"].append({"source": "OSM_ACCESS", "success": True, "mode": origin,
                                   "cities": list(INDICATOR_CITIES), "sha256": indicator_digest(text.encode("utf-8"))})
    receipt["completed"] = indicator_timestamp()
    result = indicator_build(files, income, pois, receipt, raw_sources)
    receipt["coverage"] = indicator_copy.deepcopy(result["metadata"]["coverage"])
    result["metadata"]["acquisitionReceipt"] = indicator_copy.deepcopy(receipt)
    return result, receipt, raw_sources


def indicator_request(url, timeout=30, data=None):
    headers = {"User-Agent": "Vagvis/2.3.0 (municipal planning research; https://www.scb.se/deso)",
               "Accept": "application/json,application/zip,*/*"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    request = indicator_urlrequest.Request(url, headers=headers, data=data)
    with indicator_urlrequest.urlopen(request, timeout=timeout) as response:
        raw = response.read(INDICATOR_MAX_BYTES + 1)
    if len(raw) > INDICATOR_MAX_BYTES:
        raise ValueError("Datakällans svar överstiger 100 MB.")
    return raw


def indicator_fetch_income(expected):
    try:
        raw = indicator_request(INDICATOR_SCB_URL, timeout=30)
        obj = indicator_parse_income_zip(raw, expected)
        return {"source": "SCB_TAB6684", "success": True, "url": INDICATOR_SCB_URL,
                "bytes": len(raw), "sha256": indicator_digest(raw), "parsed": obj}
    except Exception as error:
        return {"source": "SCB_TAB6684", "success": False, "url": INDICATOR_SCB_URL,
                "error": type(error).__name__ + ": " + str(error)}


def indicator_fetch_city(city):
    failures, query = [], indicator_access_query(city)
    for endpoint in INDICATOR_OVERPASS:
        try:
            raw = indicator_request(endpoint, timeout=30, data=indicator_urlparse.urlencode({"data": query}).encode("utf-8"))
            points, stamp = indicator_parse_osm(raw, city)
            return {"success": True, "city": city, "url": endpoint, "query": query, "sha256": indicator_digest(raw),
                    "points": points, "osmTimestamp": stamp, "raw": raw, "failures": failures,
                    "queryVerified": True,
                    **indicator_osm_export_info(raw)}
        except Exception as error:
            failures.append({"url": endpoint, "error": type(error).__name__ + ": " + str(error)})
    return {"success": False, "city": city, "query": query, "failures": failures}


def indicator_fetch(files, on_progress=None):
    """Fetch independent sources once; retain old values whenever a source fails."""
    _, stock, _ = indicator_context(files)
    receipt, raw_sources = indicator_receipt("streamlit_explicit_fetch"), {}
    if on_progress:
        on_progress("Hämtar SCB:s inkomstdata och kartpunkter för tre kommuner …")
    with indicator_ThreadPoolExecutor(max_workers=4) as pool:
        income_future = pool.submit(indicator_fetch_income, stock)
        city_futures = {city: pool.submit(indicator_fetch_city, city) for city in INDICATOR_CITIES}
        income_result = income_future.result()
        cities = {city: future.result() for city, future in city_futures.items()}
    receipt["sources"].append({key: value for key, value in income_result.items() if key != "parsed"})
    income = income_result.get("parsed") if income_result.get("success") else None
    if income is not None:
        raw_sources["underlag/korstracka/inkomst_2024.json"] = indicator_json_text(income)
    osm_ok = all(result.get("success") for result in cities.values())
    receipt["sources"].append({"source": "OSM_ACCESS", "success": osm_ok,
                               "cities": [{key: value for key, value in cities[city].items() if key not in ("points", "raw")}
                                          for city in INDICATOR_CITIES]})
    pois = indicator_osm_document(cities) if osm_ok else None
    if pois is not None:
        raw_sources["underlag/korstracka/osm_access_points.json"] = indicator_json_text(pois)
    receipt["completed"] = indicator_timestamp()
    result = indicator_build(files, income, pois, receipt, raw_sources)
    receipt["coverage"] = indicator_copy.deepcopy(result["metadata"]["coverage"])
    result["metadata"]["acquisitionReceipt"] = indicator_copy.deepcopy(receipt)
    if on_progress:
        on_progress("Källdata kontrollerade. Se täckning och hämtningskvitto nedan.")
    return result, receipt, raw_sources


def indicator_method_receipt(text, indicator, receipt):
    labels = {"Avstånd till valt centrum": "centreKm", "Avstånd till kollektivtrafikpunkt": "transitKm",
              "Avstånd till service eller dagligvaror": "serviceKm", "Median ekonomisk standard": "income",
              "Registrerade bilar per hushåll": "bph"}
    coverage = indicator.get("metadata", {}).get("coverage", {})
    for label, field in labels.items():
        value = coverage.get(field, {})
        count = str(value.get("available", 0)) + " / " + str(value.get("total", len(indicator["rows"])))
        pattern = r"(<tr>\s*<td>" + indicator_re.escape(label) + r"</td>\s*<td>)[^<]*(</td>)"
        text = indicator_re.sub(pattern, lambda match: match[1] + count + match[2], text)
    text = indicator_re.sub(r'<article\b[^>]*\bid=["\']indicator-acquisition-receipt["\'][^>]*>.*?</article>',
                            "", text, flags=indicator_re.IGNORECASE | indicator_re.DOTALL)
    escaped = indicator_html.escape
    rows = "".join("<tr><td>" + escaped(str(source.get("source", "Datakälla"))) + "</td><td>" +
                   ("Hämtad och validerad" if source.get("success") else "Kunde inte kompletteras; tidigare värden behålls") +
                   "</td></tr>" for source in receipt.get("sources", []))
    years = indicator.get("metadata", {}).get("sourceYears", {})
    article = ('<article class="card" id="indicator-acquisition-receipt"><h2>Senaste komplettering av DeSO-indikatorer</h2>'
               '<p>Kontrollerad ' + escaped(str(receipt.get("completed", "inte angivet"))) +
               '. Beräkningsår 2024, DeSO 2025. Inkomst avser median ekonomisk standard per konsumtionsenhet i tkr, '
               'inte bilägarnas individuella årslön. Service avser dagligvaruhandel. Kollektivtrafik avser fysisk större nod '
               'och anger varken turtäthet eller restid.</p><p>Inkomstens uppgiftsår: ' + escaped(str(years.get("income"))) +
               '. OSM-källans tidsstämpel: ' + escaped(str(years.get("pois"))) +
               '. Saknade värden är fortsatt null/neutrala. Variablernas koefficienter är valda antaganden; '
               'hämtningen skattar inga nya samband.</p><table><thead><tr><th>Källa</th><th>Utfall vid denna komplettering</th>'
               '</tr></thead><tbody>' + rows + '</tbody></table></article>')
    match = indicator_re.search(r"</body\s*>", text, flags=indicator_re.IGNORECASE)
    return text[:match.start()] + article + text[match.start():] if match else text + article


def indicator_updated_bundle(bundle, indicator, receipt, raw_sources):
    if not isinstance(bundle, dict) or bundle.get("schema") != "vagvis-flat-bundle-1":
        raise ValueError("Fel format på det samlade Vägvis-paketet.")
    candidate = indicator_copy.deepcopy(bundle)
    files = candidate.get("files")
    if not isinstance(files, dict):
        raise ValueError("Modellpaketets filer saknas.")
    files[INDICATOR_DATA_PATH] = indicator_json_text(indicator)
    files[INDICATOR_RECEIPT_PATH] = indicator_json_text(receipt)
    for name, text in raw_sources.items():
        if (not isinstance(name, str) or not name.startswith("underlag/korstracka/") or ".." in name.split("/")
                or not isinstance(text, str)):
            raise ValueError("Ogiltig sökväg eller text i datakällornas underlag.")
        files[name] = text
    method_path = "app/Metod_och_anvandning.html"
    if method_path in files:
        files[method_path] = indicator_method_receipt(files[method_path], indicator, receipt)
    indicator_context(files)
    candidate["sha256"] = {name: indicator_digest(text.encode("utf-8")) for name, text in files.items()}
    candidate["packagedDate"] = str(receipt.get("completed", indicator_timestamp()))[:10]
    candidate["indicatorAcquisition"] = indicator_copy.deepcopy(receipt)
    if len(indicator_json_text(candidate).encode("utf-8")) >= 25 * 1024 * 1024:
        raise ValueError("Det samlade datapaketet blir för stort för GitHubs webbuppladdning (25 MiB).")
    return candidate


if __name__ == "__main__":
    main()
