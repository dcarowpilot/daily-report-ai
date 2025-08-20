import streamlit as st
from supabase import create_client
from datetime import date, datetime

# -------------------------
# CONFIG — change for your project
# -------------------------
BUCKET_NAME = "daily-report-photos"   # <- your bucket name
PROJECT_OPTIONS = ["Site A", "Site B", "Demo Project"]  # <- your projects

# -------------------------
# INIT: Supabase client
# -------------------------
@st.cache_resource
def get_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = get_supabase()

st.set_page_config(page_title="Daily Report AI", page_icon="📝")
st.title("📝 Daily Report (MVP)")
st.caption("Crude but real: voice/text + photos → one structured row in Supabase.")

# -------------------------
# Helpers
# -------------------------
def str_to_list(s: str):
    if not s or not s.strip():
        return []
    parts = [p.strip() for p in s.replace("\n", ",").replace(";", ",").split(",")]
    return [p for p in parts if p]

def kvlist_to_json(s: str, kv_sep=":", item_sep=",", crew_hint=False):
    """
    'Carpenters:6, Ironworkers:4' -> [{"trade":"Carpenters","count":6}, ...]
    'Excavator:2, Telehandler:1'  -> [{"type":"Excavator","count":2}, ...]
    """
    out = []
    if not s or not s.strip():
        return out
    items = [x.strip() for x in s.replace("\n", item_sep).split(item_sep) if x.strip()]
    for it in items:
        if kv_sep in it:
            k, v = [x.strip() for x in it.split(kv_sep, 1)]
            try:
                num = int(v)
            except:
                try:
                    num = float(v)
                except:
                    num = v
            out.append({"trade" if crew_hint else "type": k, "count": num})
    return out

def qty_to_json(s: str):
    out = []
    if not s or not s.strip():
        return out
    lines = [x.strip() for x in s.splitlines() if x.strip()]
    for line in lines:
        if ":" not in line:
            continue
        left, val = [x.strip() for x in line.split(":", 1)]
        parts = left.split()
        if len(parts) >= 2 and parts[-1].isalpha():
            item = " ".join(parts[:-1])
            unit = parts[-1]
        else:
            item, unit = left, ""
        try:
            value = float(val)
        except:
            value = val
        out.append({"item": item, "unit": unit, "value": value})
    return out

def upload_photo_to_bucket(st_file, project_name: str, report_date: date) -> str:
    safe_proj = project_name.replace("/", "-")
    ts = int(datetime.utcnow().timestamp())
    name = st_file.name
    path = f"{report_date.isoformat()}/{safe_proj}/{ts}_{name}".replace(" ", "_")
    file_bytes = st_file.getvalue()
    content_type = st_file.type or "application/octet-stream"
    res = supabase.storage.from_(BUCKET_NAME).upload(
        path, file_bytes, file_options={"content-type": content_type, "upsert": False}
    )
    if hasattr(res, "status_code") and res.status_code and res.status_code >= 400:
        raise RuntimeError(f"Upload failed: {res}")
    return supabase.storage.from_(BUCKET_NAME).get_public_url(path)

# -------------------------
# FORM KEYS & RESET
# -------------------------
FORM_KEYS = [
    "f_date", "f_project", "f_weather", "f_author", "f_crew_text", "f_equip_text",
    "f_activities_text", "f_quantities_text", "f_subs_text", "f_safety_text",
    "f_issues_text", "f_notes_raw"
]

if "nonce" not in st.session_state:
    st.session_state["nonce"] = 0   # for file_uploader reset

def clear_form():
    # Remove all widget values; on rerun they re-init to defaults
    for k in FORM_KEYS:
        st.session_state.pop(k, None)
    # bump uploader nonce so it becomes a fresh widget
    st.session_state["nonce"] += 1

# -------------------------
# UI: Form
# -------------------------
with st.form("daily_form", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        report_date = st.date_input("Date", value=date.today(), key="f_date")
    with col2:
        # default to first item if no previous choice
        current_idx = 0
        if "f_project" in st.session_state and st.session_state["f_project"] in PROJECT_OPTIONS:
            current_idx = PROJECT_OPTIONS.index(st.session_state["f_project"])
        project = st.selectbox("Project", PROJECT_OPTIONS, index=current_idx, key="f_project")

    weather = st.text_input("Weather (free text)", placeholder="Sunny, 75°F, light wind", key="f_weather")
    author = st.text_input("Author", placeholder="Jane Superintendent", key="f_author")

    st.markdown("**Crew counts** *(Trade:Count, Trade:Count)*")
    crew_text = st.text_area("e.g., Carpenters:6, Ironworkers:4", height=60, key="f_crew_text")

    st.markdown("**Equipment** *(Type:Count, Type:Count)*")
    equip_text = st.text_area("e.g., Excavator:2, Telehandler:1", height=60, key="f_equip_text")

    st.markdown("**Activities (free text or bullets)**")
    activities_text = st.text_area("e.g., Formed footings at Grid A; Poured slab at Area 3", height=90, key="f_activities_text")

    st.markdown("**Quantities** *(one per line: Item [Unit]: Value)*")
    quantities_text = st.text_area("e.g., Concrete CY: 35\nLF curb: 120", height=80, key="f_quantities_text")

    subs_text = st.text_input("Subcontractors present (comma-separated)", placeholder="ACME Paving, XYZ Steel", key="f_subs_text")
    safety_text = st.text_area("Safety observations", height=80, key="f_safety_text")
    issues_text = st.text_area("Issues / delays", height=80, key="f_issues_text")

    # File uploader with dynamic key so it resets after submit
    photos = st.file_uploader(
        "Photos",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"f_photos_{st.session_state['nonce']}",
    )

    notes_raw = st.text_area("Raw notes (optional)", placeholder="Paste any raw notes here", height=80, key="f_notes_raw")

    # Submit button
    submitted = st.form_submit_button("Submit report")

if submitted:
    with st.spinner("Uploading photos and saving report..."):
        # 1) Upload photos (if any)
        photo_urls = []
        if photos:
            for p in photos:
                try:
                    url = upload_photo_to_bucket(p, project, report_date)
                    photo_urls.append(url)
                except Exception as e:
                    st.warning(f"Photo upload failed for {p.name}: {e}")

        # 2) Build structured payload
        crew_counts = kvlist_to_json(crew_text, crew_hint=True)
        equipment = kvlist_to_json(equip_text)
        activities = []
        if activities_text.strip():
            parts = [x.strip() for x in activities_text.replace("\n", ";").split(";") if x.strip()]
            activities = [{"location": "", "description": p} for p in parts]
        quantities = qty_to_json(quantities_text)
        subs_present = str_to_list(subs_text)

        row = {
            "date": report_date.isoformat(),
            "project": project,
            "author": author,
            "weather": weather,
            "crew_counts": crew_counts,
            "equipment": equipment,
            "activities": activities,
            "quantities": quantities,
            "subs_present": subs_present,   # jsonb recommended
            "issues_delays": issues_text,
            "safety": safety_text,
            "photos": photo_urls,           # jsonb recommended
            "notes_raw": notes_raw,
            "doc_url": "",
        }

        # 3) Insert to Supabase
        try:
            supabase.table("daily_reports").insert(row).execute()
            st.success("✅ Report saved to Supabase.")
            clear_form()
            st.rerun()  # re-render with cleared state and a fresh uploader key
        except Exception as e:
            st.error(f"❌ Could not insert row: {e}")