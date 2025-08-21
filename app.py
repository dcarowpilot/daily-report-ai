import streamlit as st
from supabase import create_client
from datetime import date, datetime
from openai import OpenAI
import tempfile
from pathlib import Path
from st_audiorec import st_audiorec  # <— mic recorder component

# -------------------------
# CONFIG — change for your project
# -------------------------
BUCKET_NAME = "daily-report-photos"          # <- your bucket name
PROJECT_OPTIONS = ["Site A", "Site B", "Demo Project"]  # <- your projects

# -------------------------
# INIT: Supabase + OpenAI
# -------------------------
@st.cache_resource
def get_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

@st.cache_resource
def get_openai():
    return OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

supabase = get_supabase()
client = get_openai()

st.set_page_config(page_title="Daily Report AI", page_icon="📝")
st.title("📝 Daily Report (MVP)")
st.caption("Voice → Whisper → appended to Notes • Photos → Supabase • Data → daily_reports")

# -------------------------
# Session: nonce to fully reset the form
# -------------------------
if "nonce" not in st.session_state:
    st.session_state["nonce"] = 0
if "recorded_audio" not in st.session_state:
    st.session_state["recorded_audio"] = None  # bytes of WAV

def reset_all_fields():
    st.session_state["nonce"] += 1
    st.session_state["recorded_audio"] = None

# -------------------------
# Helpers
# -------------------------
def str_to_list(s: str):
    if not s or not s.strip():
        return []
    parts = [p.strip() for p in s.replace("\n", ",").replace(";", ",").split(",")]
    return [p for p in parts if p]

def kvlist_to_json(s: str, kv_sep=":", item_sep=",", crew_hint=False):
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
            item = " ".join(parts[:-1]); unit = parts[-1]
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

def transcribe_wav_bytes(wav_bytes: bytes) -> str:
    """Send WAV bytes to Whisper and return transcript text."""
    if not wav_bytes:
        return ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(wav_bytes)
            tmp.flush()
            with open(tmp.name, "rb") as f:
                resp = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                )
        return resp.text or ""
    except Exception as e:
        st.warning(f"Transcription failed: {e}")
        return ""

# -------------------------
# 🎙️ Recorder (outside the form)
# -------------------------
st.subheader("🎙️ Voice note (optional)")
st.caption("Tap to record, speak, tap again to stop. The transcript will be appended to Notes.")

recorded_bytes = st_audiorec()  # shows a round record/stop button; returns WAV bytes when stopped

# Save to session when a fresh recording arrives
if recorded_bytes and recorded_bytes != st.session_state.get("recorded_audio"):
    st.session_state["recorded_audio"] = recorded_bytes
    st.success("Captured voice note.")

cols = st.columns([1,1,3])
with cols[0]:
    if st.session_state["recorded_audio"]:
        st.audio(st.session_state["recorded_audio"], format="audio/wav")
with cols[1]:
    if st.session_state["recorded_audio"]:
        if st.button("Clear recording"):
            st.session_state["recorded_audio"] = None
            st.rerun()

st.divider()

# -------------------------
# UI: Form (dynamic key so it resets)
# -------------------------
FORM_KEY = f"daily_form_{st.session_state['nonce']}"

with st.form(FORM_KEY, clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        report_date = st.date_input("Date", value=date.today())
    with col2:
        project = st.selectbox("Project", PROJECT_OPTIONS, index=0)

    weather = st.text_input("Weather (free text)", placeholder="Sunny, 75°F, light wind")
    author = st.text_input("Author", placeholder="Jane Superintendent")

    st.markdown("**Crew counts** *(Trade:Count, Trade:Count)*")
    crew_text = st.text_area("e.g., Carpenters:6, Ironworkers:4", height=60)

    st.markdown("**Equipment** *(Type:Count, Type:Count)*")
    equip_text = st.text_area("e.g., Excavator:2, Telehandler:1", height=60)

    st.markdown("**Activities (free text or bullets)**")
    activities_text = st.text_area("e.g., Formed footings at Grid A; Poured slab at Area 3", height=90)

    st.markdown("**Quantities** *(one per line: Item [Unit]: Value)*")
    quantities_text = st.text_area("e.g., Concrete CY: 35\nLF curb: 120", height=80)

    subs_text = st.text_input("Subcontractors present (comma-separated)", placeholder="ACME Paving, XYZ Steel")
    safety_text = st.text_area("Safety observations", height=80)
    issues_text = st.text_area("Issues / delays", height=80)

    photos = st.file_uploader(
        "Photos",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"photos_{st.session_state['nonce']}",
    )

    notes_raw = st.text_area("Raw notes (optional)", placeholder="Paste any raw notes here", height=80)

    submitted = st.form_submit_button("Submit report")

if submitted:
    with st.spinner("Transcribing, uploading photos, and saving report..."):
        # 0) Transcribe recorded audio (append to notes)
        transcript = transcribe_wav_bytes(st.session_state.get("recorded_audio"))
        if transcript:
            notes_raw = (notes_raw + "\n" if notes_raw else "") + transcript

        # 1) Upload photos
        photo_urls = []
        if photos:
            for p in photos:
                try:
                    url = upload_photo_to_bucket(p, project, report_date)
                    photo_urls.append(url)
                except Exception as e:
                    st.warning(f"Photo upload failed for {p.name}: {e}")

        # 2) Build row
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
            "subs_present": subs_present,
            "issues_delays": issues_text,
            "safety": safety_text,
            "photos": photo_urls,
            "notes_raw": notes_raw,   # includes transcript
            "doc_url": "",
        }

        # 3) Insert
        try:
            supabase.table("daily_reports").insert(row).execute()
            st.success("✅ Report saved (with transcription if recorded).")
            reset_all_fields()     # clears recorder + form
            st.rerun()
        except Exception as e:
            st.error(f"❌ Could not insert row: {e}")