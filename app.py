import streamlit as st
from supabase import create_client
from datetime import date, datetime
import json
import io

# -------------------------
# CONFIG — change for your project
# -------------------------
BUCKET_NAME = "photos"   # <- put your bucket name here
PROJECT_OPTIONS = ["Site A", "Site B", "Demo Project"]  # <- change to your real projects

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
    """
    Turns comma or semicolon separated text into a list of trimmed strings.
    e.g. "ACME Paving, XYZ Steel" -> ["ACME Paving","XYZ Steel"]
    """
    if not s or not s.strip():
        return []
    parts = [p.strip() for p in s.replace("\n", ",").replace(";", ",").split(",")]
    return [p for p in parts if p]

def kvlist_to_json(s: str, kv_sep=":", item_sep=","):
    """
    Turns lines like 'Carpenters:6, Ironworkers:4' into
    [{"trade":"Carpenters","count":6}, ...]
    """
    out = []
    if not s or not s.strip():
        return out
    # support newlines or commas as separators
    items = [x.strip() for x in s.replace("\n", item_sep).split(item_sep) if x.strip()]
    for it in items:
        if kv_sep in it:
            k, v = [x.strip() for x in it.split(kv_sep, 1)]
            try:
                count = int(v)
            except:
                # try float, then fallback to string
                try:
                    count = float(v)
                except:
                    count = v
            out.append({"trade" if "crew" in s.lower() else "type": k, "count": count})
    return out

def qty_to_json(s: str):
    """
    Turns lines like:
      LF curb: 120
      CY concrete: 35
    into [{"item":"LF curb","unit":"","value":120}, ...]
    If you include units like 'Concrete CY: 35', it still works.
    """
    out = []
    if not s or not s.strip():
        return out
    lines = [x.strip() for x in s.splitlines() if x.strip()]
    for line in lines:
        if ":" in line:
            left, val = [x.strip() for x in line.split(":", 1)]
            # Try to split unit from item if present (very loose)
            parts = left.split()
            if len(parts) >= 2 and parts[-1].isalpha():
                item = " ".join(parts[:-1])
                unit = parts[-1]
            else:
                item = left
                unit = ""
            try:
                value = float(val)
            except:
                # allow values like "35 +/- 2"
                value = val
            out.append({"item": item, "unit": unit, "value": value})
    return out

def upload_photo_to_bucket(st_file, project_name: str, report_date: date) -> str:
    """
    Uploads a Streamlit UploadedFile to Supabase Storage and returns a public URL.
    Assumes public bucket for MVP.
    """
    # Build a tidy path, e.g., 2025-08-18/Site A/1700000000_photo.jpg
    safe_proj = project_name.replace("/", "-")
    ts = int(datetime.utcnow().timestamp())
    # Preserve extension
    name = st_file.name
    ext = ""
    if "." in name:
        ext = "." + name.split(".")[-1].lower()
    path = f"{report_date.isoformat()}/{safe_proj}/{ts}_{name}".replace(" ", "_")

    # Upload bytes
    file_bytes = st_file.getvalue()
    res = supabase.storage.from_(BUCKET_NAME).upload(path, file_bytes)
    if hasattr(res, "status_code") and res.status_code >= 400:
        raise RuntimeError(f"Upload failed: {res}")
    # Get public URL
    public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(path)
    return public_url

# -------------------------
# UI: Form
# -------------------------
with st.form("daily_form", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        report_date = st.date_input("Date", value=date.today())
    with col2:
        project = st.selectbox("Project", PROJECT_OPTIONS, index=0)

    weather = st.text_input("Weather (free text)", placeholder="Sunny, 75°F, light wind")
    author = st.text_input("Author", placeholder="Jane Superintendent")

    st.markdown("**Crew counts** *(format: Trade:Count, Trade:Count)*")
    crew_text = st.text_area("e.g., Carpenters:6, Ironworkers:4", height=60)

    st.markdown("**Equipment** *(format: Type:Count, Type:Count)*")
    equip_text = st.text_area("e.g., Excavator:2, Telehandler:1", height=60)

    st.markdown("**Activities (free text or bullets)**")
    activities_text = st.text_area("e.g., Formed footings at Grid A; Poured slab at Area 3", height=90)

    st.markdown("**Quantities** *(one per line: Item [Unit]: Value)*")
    quantities_text = st.text_area("e.g., Concrete CY: 35\nLF curb: 120", height=80)

    subs_text = st.text_input("Subcontractors present (comma-separated)", placeholder="ACME Paving, XYZ Steel")
    safety_text = st.text_area("Safety observations", height=80)
    issues_text = st.text_area("Issues / delays", height=80)

    photos = st.file_uploader("Photos", type=["jpg","jpeg","png"], accept_multiple_files=True)
    notes_raw = st.text_area("Raw notes (optional)", placeholder="Paste any raw notes here", height=80)

    submitted = st.form_submit_button("Submit report")

if submitted:
    with st.spinner("Uploading photos and saving report..."):
        # 1) Upload photos (optional)
        photo_urls = []
        if photos:
            for p in photos:
                try:
                    url = upload_photo_to_bucket(p, project, report_date)
                    photo_urls.append(url)
                except Exception as e:
                    st.warning(f"Photo upload failed for {p.name}: {e}")

        # 2) Build structured payload
        crew_counts = kvlist_to_json(crew_text)
        equipment = kvlist_to_json(equip_text)
        # Activities: keep as simple list of sentences for now
        activities = []
        if activities_text.strip():
            # split on semicolons or newlines for crude bullets
            parts = [x.strip() for x in activities_text.replace("\n", ";").split(";") if x.strip()]
            activities = [{"location":"", "description": p} for p in parts]

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
            "subs_present": subs_present,   # if your column is jsonb; if text[] change insert accordingly
            "issues_delays": issues_text,
            "safety": safety_text,
            "photos": photo_urls,           # if your column is jsonb; if text[] change insert accordingly
            "notes_raw": notes_raw,
            "doc_url": "",                  # we’ll fill after doc generation later
        }

        # 3) Insert to Supabase
        try:
            resp = supabase.table("daily_reports").insert(row).execute()
            st.success("✅ Report saved to Supabase.")
            st.json(row)
        except Exception as e:
            st.error(f"❌ Could not insert row: {e}")