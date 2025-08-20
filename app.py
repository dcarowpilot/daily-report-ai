import streamlit as st
from supabase import create_client
from datetime import date, datetime

# -------------------------
# CONFIG — change for your project
# -------------------------
BUCKET_NAME = "daily-report-photos"   # <- your bucket
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
            out.append(
                {"trade" if crew_hint else "type": k, "count": num}
            )
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
    # Optional: set content-type from upload
    content_type = st_file.type or "application/octet-stream"
    res = supabase.storage.from_(BUCKET_NAME).upload(
        path, file_bytes, file_options={"content-type": content_type, "upsert": False}
    )
    if hasattr(res, "status_code") and res.status_code >= 400:
        raise RuntimeError(f"Upload failed: {res}")
    return supabase.storage.from_(BUCKET_NAME).get_public_url(path)

# -------------------------
# DEFAULTS & SESSION KEYS
# -------------------------
def ensure_defaults():
    defaults = {
        "date": date.today(),
        "project": PROJECT_OPTIONS[0],
        "weather": "",
        "author": "",
        "crew_text": "",
        "equip_text": "",
        "activities_text": "",
        "quantities_text": "",
        "subs_text": "",
        "safety_text": "",
        "issues_text": "",
        "notes_raw": "",
        "photos": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def clear_form():
    st.session_state["date"] = date.today()
    st.session_state["project"] = PROJECT_OPTIONS[0]
    for k in [
        "weather", "author", "crew_text", "equip_text", "activities_text",
        "quantities_text", "subs_text", "safety_text", "issues_text", "notes_raw"
    ]:
        st.session_state[k] = ""
    st.session_state["photos"] = None

ensure_defaults()

# -------------------------
# UI: Form
# -------------------------
with st.form("daily_form", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        report_date = st.date_input("Date", value=st.session_state["date"], key="date")
    with col2:
        project = st.selectbox("Project", PROJECT_OPTIONS, index=PROJECT_OPTIONS.index(st.session_state["project"]), key="project")

    weather = st.text_input("Weather (free text)", placeholder="Sunny, 75°F, light wind", key="weather")
    author = st.text_input("Author", placeholder="Jane Superintendent", key="author")

    st.markdown("**Crew counts** *(Trade:Count, Trade:Count)*")
    crew_text = st.text_area("e.g., Carpenters:6, Ironworkers:4", height=60, key="crew_text")

    st.markdown("**Equipment** *(Type:Count, Type:Count)*")
    equip_text = st.text_area("e.g., Excavator:2, Telehandler:1", height=60, key="equip_text")

    st.markdown("**Activities (free text or bullets)**")
    activities_text = st.text_area("e.g., Formed footings at Grid A; Poured slab at Area 3", height=90, key="activities_text")

    st.markdown("**Quantities** *(one per line: Item [Unit]: Value)*")
    quantities_text = st.text_area("e.g., Concrete CY: 35\nLF curb: 120", height=80, key="quantities_text")

    subs_text = st.text_input("Subcontractors present (comma-separated)", placeholder="ACME Paving, XYZ Steel", key="subs_text")
    safety_text = st.text_area("Safety observations", height=80, key="safety_text")
    issues_text = st.text_area("Issues / delays", height=80, key="issues_text")

    photos = st.file_uploader("Photos", type=["jpg","jpeg","png"], accept_multiple_files=True, key="photos")
    notes_raw = st.text_area("Raw notes (optional)", placeholder="Paste any raw notes here", height=80, key="notes_raw")

    submitted = st.form_submit_button("Submit report")

if submitted:
    with st.spinner("Uploading photos and saving report..."):
        # 1) Upload photos (if any)
        photo_urls = []
        if st.session_state["photos"]:
            for p in st.session_state["photos"]:
                try:
                    url = upload_photo_to_bucket(p, st.session_state["project"], st.session_state["date"])
                    photo_urls.append(url)
                except Exception as e:
                    st.warning(f"Photo upload failed for {p.name}: {e}")

        # 2) Build structured payload
        crew_counts = kvlist_to_json(st.session_state["crew_text"], crew_hint=True)
        equipment = kvlist_to_json(st.session_state["equip_text"])
        activities = []
        if st.session_state["activities_text"].strip():
            parts = [x.strip() for x in st.session_state["activities_text"].replace("\n", ";").split(";") if x.strip()]
            activities = [{"location": "", "description": p} for p in parts]
        quantities = qty_to_json(st.session_state["quantities_text"])
        subs_present = str_to_list(st.session_state["subs_text"])

        row = {
            "date": st.session_state["date"].isoformat(),
            "project": st.session_state["project"],
            "author": st.session_state["author"],
            "weather": st.session_state["weather"],
            "crew_counts": crew_counts,
            "equipment": equipment,
            "activities": activities,
            "quantities": quantities,
            "subs_present": subs_present,   # jsonb recommended
            "issues_delays": st.session_state["issues_text"],
            "safety": st.session_state["safety_text"],
            "photos": photo_urls,           # jsonb recommended
            "notes_raw": st.session_state["notes_raw"],
            "doc_url": "",
        }

        # 3) Insert to Supabase
        try:
            supabase.table("daily_reports").insert(row).execute()
            st.success("✅ Report saved to Supabase.")
            # 4) Clear inputs AFTER successful insert
            clear_form()
        except Exception as e:
            st.error(f"❌ Could not insert row: {e}")