import streamlit as st
from supabase import create_client
from datetime import date, datetime
from openai import OpenAI
from st_audiorec import st_audiorec
import tempfile
import hashlib
import json

# =========================
# CONFIG (edit these)
# =========================
BUCKET_PHOTOS = "daily-report-photos"
BUCKET_AUDIO  = "daily-report-audio"
PROJECT_OPTIONS = ["Site A", "Site B", "Demo Project"]

# =========================
# INIT CLIENTS
# =========================
@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

@st.cache_resource
def get_openai():
    return OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

supabase = get_supabase()
openai_client = get_openai()

st.set_page_config(page_title="Daily Report AI", page_icon="📝")
st.title("📝 Daily Report (MVP)")
st.caption("Record → Whisper → review transcript • Auto-structure with GPT • Photos/Audio → Supabase • Data → daily_reports")

# =========================
# SESSION
# =========================
if "nonce" not in st.session_state:
    st.session_state["nonce"] = 0
if "recorded_audio" not in st.session_state:
    st.session_state["recorded_audio"] = None
if "transcript_prefill" not in st.session_state:
    st.session_state["transcript_prefill"] = ""      # safe prefill holder (not a widget key)
if "audio_hash" not in st.session_state:
    st.session_state["audio_hash"] = None            # md5 of last processed bytes
if "skip_record_once" not in st.session_state:
    st.session_state["skip_record_once"] = False     # ignore first recorder echo after reset

def reset_all_fields():
    """Reset recorder + transcript and rebuild all widgets by bumping nonce.
       Also ignore the first recorder output on the next run (component echo)."""
    st.session_state["recorded_audio"] = None
    st.session_state["transcript_prefill"] = ""
    st.session_state["audio_hash"] = None
    st.session_state["skip_record_once"] = True
    st.session_state["nonce"] += 1

# =========================
# HELPERS
# =========================
def str_to_list(s: str):
    if not s or not s.strip(): return []
    parts = [p.strip() for p in s.replace("\n", ",").replace(";", ",").split(",")]
    return [p for p in parts if p]

def kvlist_to_json(s: str, kv_sep=":", item_sep=",", crew_hint=False):
    out = []
    if not s or not s.strip(): return out
    items = [x.strip() for x in s.replace("\n", item_sep).split(item_sep) if x.strip()]
    for it in items:
        if kv_sep in it:
            k, v = [x.strip() for x in it.split(kv_sep, 1)]
            try: num = int(v)
            except:
                try: num = float(v)
                except: num = v
            out.append({"trade" if crew_hint else "type": k, "count": num})
    return out

def qty_to_json(s: str):
    out = []
    if not s or not s.strip(): return out
    for line in [x.strip() for x in s.splitlines() if x.strip()]:
        if ":" not in line: continue
        left, val = [x.strip() for x in line.split(":", 1)]
        parts = left.split()
        if len(parts) >= 2 and parts[-1].isalpha():
            item = " ".join(parts[:-1]); unit = parts[-1]
        else:
            item, unit = left, ""
        try: value = float(val)
        except: value = val
        out.append({"item": item, "unit": unit, "value": value})
    return out

def upload_bytes_to_bucket(bucket: str, path: str, data: bytes, content_type: str) -> str:
    """Uploads bytes to Supabase Storage; returns public URL or ''."""
    try:
        res = supabase.storage.from_(bucket).upload(
            path,
            data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        status = getattr(res, "status_code", None)
        if status and status >= 400:
            raise RuntimeError(f"Upload failed {status}: {getattr(res, 'message', '')}")
        return supabase.storage.from_(bucket).get_public_url(path)
    except Exception as e:
        st.warning(f"Upload error for {bucket}/{path}: {e}")
        return ""

def upload_photo(st_file, project_name: str, report_date: date) -> str:
    safe_proj = project_name.replace("/", "-")
    ts = int(datetime.utcnow().timestamp())
    name = st_file.name.replace(" ", "_")
    path = f"{report_date.isoformat()}/{safe_proj}/{ts}_{name}"
    return upload_bytes_to_bucket(
        BUCKET_PHOTOS, path, st_file.getvalue(), st_file.type or "application/octet-stream"
    )

def upload_audio_bytes(project_name: str, report_date: date, wav_bytes: bytes) -> str:
    if not wav_bytes: return ""
    safe_proj = project_name.replace("/", "-")
    ts = int(datetime.utcnow().timestamp())
    path = f"{report_date.isoformat()}/{safe_proj}/{ts}_voice.wav"
    return upload_bytes_to_bucket(BUCKET_AUDIO, path, wav_bytes, "audio/wav")

def transcribe_wav_bytes(wav_bytes: bytes) -> str:
    """Send WAV bytes to Whisper and return transcript text."""
    if not wav_bytes: return ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(wav_bytes); tmp.flush()
            with open(tmp.name, "rb") as f:
                resp = openai_client.audio.transcriptions.create(model="whisper-1", file=f)
        return resp.text or ""
    except Exception as e:
        st.warning(f"Transcription failed: {e}")
        return ""

def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()

# -------------------------
# LLM extraction helpers
# -------------------------
def _extract_output_text(resp) -> str:
    """Be tolerant of SDK return shapes; try a few ways to get text."""
    if hasattr(resp, "output_text"):
        return resp.output_text
    try:
        return resp.output[0].content[0].text
    except Exception:
        pass
    try:
        return resp.choices[0].message.content
    except Exception:
        pass
    return ""

def extract_structured_with_gpt(client, raw_text: str) -> dict:
    """
    Calls GPT to convert messy notes/transcripts into normalized JSON.
    Returns a dict with keys: crew_counts, equipment, activities, quantities, safety, issues_delays
    """
    if not raw_text or not raw_text.strip():
        return {}

    schema = {
        "type": "object",
        "properties": {
            "crew_counts": {"type": "array", "items": {
                "type": "object",
                "properties": {"trade": {"type": "string"}, "count": {"type": "number"}},
                "required": ["trade", "count"]
            }},
            "equipment": {"type": "array", "items": {
                "type": "object",
                "properties": {"type": {"type": "string"}, "count": {"type": "number"}},
                "required": ["type", "count"]
            }},
            "activities": {"type": "array", "items": {
                "type": "object",
                "properties": {"location": {"type": "string"}, "description": {"type": "string"}},
                "required": ["description"]
            }},
            "quantities": {"type": "array", "items": {
                "type": "object",
                "properties": {"item": {"type": "string"}, "unit": {"type": "string"}, "value": {"type": "number"}},
                "required": ["item", "value"]
            }},
            "safety": {"type": "string"},
            "issues_delays": {"type": "string"}
        },
        "required": []
    }

    system = (
        "You normalize construction daily report notes into a concise JSON summary. "
        "Infer only when explicit; otherwise omit. Keep units simple (CY, LF, SF). "
        "Return ONLY JSON matching the provided schema."
    )
    user = (
        "Free-form notes (may include transcript snippets):\n\n"
        f"{raw_text}\n\n"
        "Extract: crew_counts (trade,count), equipment (type,count), activities (location,description), "
        "quantities (item,unit,value), safety (string), issues_delays (string). "
        "If unknown, use empty arrays/strings."
    )

    try:
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=[{"role": "system", "content": system},
                   {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "daily_report_structured", "schema": schema, "strict": True},
            },
        )
        text = _extract_output_text(resp)
        data = json.loads(text)
        return {
            "crew_counts": data.get("crew_counts", []),
            "equipment": data.get("equipment", []),
            "activities": data.get("activities", []),
            "quantities": data.get("quantities", []),
            "safety": data.get("safety", ""),
            "issues_delays": data.get("issues_delays", ""),
        }
    except Exception as e:
        st.warning(f"LLM extraction failed: {e}")
        return {}

# =========================
# RECORDER + TRANSCRIPT
# =========================
st.subheader("🎙️ Voice note (optional)")
st.caption("Tap to record, speak, tap again to stop. Edit the transcript before submit.")

# Recorder (no key)
recorded_bytes = st_audiorec()

# Handle recorder output robustly:
# - Ignore the first echo after a reset (skip_record_once)
# - Only transcribe when the audio bytes hash changes
if recorded_bytes:
    new_hash = md5_bytes(recorded_bytes)
    if st.session_state.get("skip_record_once"):
        st.session_state["skip_record_once"] = False
    elif new_hash != st.session_state.get("audio_hash"):
        st.session_state["recorded_audio"] = recorded_bytes
        st.session_state["audio_hash"] = new_hash
        st.session_state["transcript_prefill"] = transcribe_wav_bytes(recorded_bytes) or ""

cols = st.columns([1,1,3])
with cols[0]:
    if st.session_state["recorded_audio"]:
        st.audio(st.session_state["recorded_audio"], format="audio/wav")
with cols[1]:
    if st.session_state["recorded_audio"]:
        if st.button("Clear recording"):
            # Reset all audio-related state and bump nonce
            reset_all_fields()
            st.success("Recording cleared.")
            st.stop()  # <- end this run cleanly; Streamlit will re-run automatically

# Transcript text area (dynamic key tied to nonce); read the returned value
transcript_text = st.text_area(
    "Transcribed audio (editable)",
    value=st.session_state.get("transcript_prefill", ""),
    key=f"transcript_{st.session_state['nonce']}",
    height=120,
    placeholder="Transcript will appear here after recording…",
)

st.divider()
use_llm = st.checkbox(
    "🔧 Auto-structure Notes + Transcript with GPT", value=True,
    help="If on, GPT will parse your free text into crew/equipment/activities/quantities/safety/issues."
)

# =========================
# FORM (dynamic key so it resets)
# =========================
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
        "Photos", type=["jpg","jpeg","png"], accept_multiple_files=True,
        key=f"photos_{st.session_state['nonce']}",
    )

    notes_raw = st.text_area("Raw notes (optional)", placeholder="Paste any raw notes here", height=80)

    submitted = st.form_submit_button("Submit report")

if submitted:
    with st.spinner("Structuring (if enabled), uploading media, and saving report..."):
        # Upload audio (even if transcript empty)
        audio_url = upload_audio_bytes(project, report_date, st.session_state.get("recorded_audio"))

        # Upload photos
        photo_urls = []
        if photos:
            for p in photos:
                try:
                    photo_urls.append(upload_photo(p, project, report_date))
                except Exception as e:
                    st.warning(f"Photo upload failed for {p.name}: {e}")

        # ---------- LLM extraction ----------
        combined_text = ""
        if notes_raw and notes_raw.strip():
            combined_text += notes_raw.strip() + "\n\n"
        if transcript_text and transcript_text.strip():
            combined_text += "Transcript:\n" + transcript_text.strip()

        extracted = {}
        if use_llm and combined_text.strip():
            with st.spinner("Structuring text with GPT…"):
                extracted = extract_structured_with_gpt(openai_client, combined_text)

        # (Optional) preview the extracted JSON
        if extracted:
            with st.expander("Preview extracted JSON"):
                st.json(extracted)

        # ---------- Merge manual inputs with extracted ----------
        crew_counts_final = kvlist_to_json(crew_text, crew_hint=True) or extracted.get("crew_counts", [])
        equipment_final   = kvlist_to_json(equip_text)                or extracted.get("equipment", [])
        if activities_text.strip():
            parts = [x.strip() for x in activities_text.replace("\n",";").split(";") if x.strip()]
            activities_final = [{"location":"", "description": p} for p in parts]
        else:
            activities_final = extracted.get("activities", [])
        quantities_final  = qty_to_json(quantities_text)              or extracted.get("quantities", [])
        safety_final      = safety_text or extracted.get("safety", "")
        issues_final      = issues_text or extracted.get("issues_delays", "")

        subs_present = str_to_list(subs_text)

        # ---------- Insert row ----------
        row = {
            "date": report_date.isoformat(),
            "project": project,
            "author": author,
            "weather": weather,
            "crew_counts": crew_counts_final,
            "equipment": equipment_final,
            "activities": activities_final,
            "quantities": quantities_final,
            "subs_present": subs_present,
            "issues_delays": issues_final,
            "safety": safety_final,
            "photos": photo_urls,
            "notes_raw": notes_raw,
            "voice_transcript": transcript_text,
            "audio_url": audio_url,
            "doc_url": "",
        }

        try:
            supabase.table("daily_reports").insert(row).execute()
            st.success("✅ Report saved (photos/audio + transcript, with GPT structuring if enabled).")
            reset_all_fields()
            st.rerun()
        except Exception as e:
            st.error(f"❌ Could not insert row: {e}")