import streamlit as st
from datetime import date, datetime
import tempfile, hashlib, json

# =========================
# Safe-Boot (use ?safe=1 to disable mic+LLM temporarily)
# =========================
SAFE_MODE = st.query_params.get("safe", ["0"])[0] == "1"

st.set_page_config(page_title="Daily Report AI", page_icon="📝")
st.title("📝 Daily Report TestZZZZ (MVP)")
if SAFE_MODE:
    st.warning("Safe-Boot mode is ON (mic + LLM disabled). Add `?safe=0` to re-enable.")

# =========================
# Imports (guarded)
# =========================
supabase = None
openai_client = None
audiorec_available = False
llm_available = False

try:
    from supabase import create_client
except Exception as e:
    st.error(f"Supabase SDK import failed: {e}")

try:
    if not SAFE_MODE:
        from openai import OpenAI
        llm_available = True
except Exception as e:
    st.warning(f"OpenAI SDK not ready (LLM disabled): {e}")
    llm_available = False

try:
    if not SAFE_MODE:
        from st_audiorec import st_audiorec
        audiorec_available = True
except Exception as e:
    st.warning(f"Audio recorder not available (mic disabled): {e}")
    audiorec_available = False

# =========================
# Clients
# =========================
@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

@st.cache_resource
def get_openai():
    return OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

try:
    supabase = get_supabase()
except Exception as e:
    st.error(f"Supabase init failed: {e}")

try:
    if llm_available:
        openai_client = get_openai()
except Exception as e:
    st.warning(f"OpenAI init failed (LLM disabled): {e}")
    llm_available = False

# =========================
# Config (edit these)
# =========================
BUCKET_PHOTOS = "daily-report-photos"
BUCKET_AUDIO  = "daily-report-audio"
PROJECT_OPTIONS = ["Site A", "Site B", "Demo Project"]

# =========================
# Session state
# =========================
if "nonce" not in st.session_state:
    st.session_state["nonce"] = 0
if "recorded_audio" not in st.session_state:
    st.session_state["recorded_audio"] = None
if "transcript_prefill" not in st.session_state:
    st.session_state["transcript_prefill"] = ""
if "audio_hash" not in st.session_state:
    st.session_state["audio_hash"] = None
if "skip_record_once" not in st.session_state:
    st.session_state["skip_record_once"] = False

# Prefill states for structured fields
for k in ["crew_prefill","equip_prefill","acts_prefill","qty_prefill","safety_prefill","issues_prefill"]:
    if k not in st.session_state:
        st.session_state[k] = ""

def reset_all_fields():
    st.session_state["recorded_audio"] = None
    st.session_state["transcript_prefill"] = ""
    st.session_state["audio_hash"] = None
    st.session_state["skip_record_once"] = True
    # clear structured prefills
    st.session_state["crew_prefill"] = ""
    st.session_state["equip_prefill"] = ""
    st.session_state["acts_prefill"] = ""
    st.session_state["qty_prefill"] = ""
    st.session_state["safety_prefill"] = ""
    st.session_state["issues_prefill"] = ""
    st.session_state["nonce"] += 1

# =========================
# Helpers
# =========================
def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()

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

def crew_to_text(crew):
    if not crew: return ""
    return ", ".join(f"{c.get('trade','')}: {c.get('count','')}" for c in crew if c)

def equip_to_text(eq):
    if not eq: return ""
    return ", ".join(f"{e.get('type','')}: {e.get('count','')}" for e in eq if e)

def acts_to_text(acts):
    if not acts: return ""
    return "; ".join(a.get("description","") if a else "" for a in acts)

def qtys_to_text(qs):
    if not qs: return ""
    lines = []
    for q in qs:
        item = q.get("item",""); unit = q.get("unit",""); val = q.get("value","")
        left = f"{item} {unit}".strip()
        lines.append(f"{left}: {val}")
    return "\n".join(lines)

# ---- Storage helpers ----
def upload_bytes_to_bucket(bucket: str, path: str, data: bytes, content_type: str) -> str:
    if not supabase: return ""
    try:
        res = supabase.storage.from_(bucket).upload(
            path, data, file_options={"content-type": content_type, "upsert": "true"}
        )
        status = getattr(res, "status_code", None)
        if status and status >= 400:
            raise RuntimeError(f"Upload failed {status}: {getattr(res, 'message', '')}")
        return supabase.storage.from_(bucket).get_public_url(path)
    except Exception as e:
        st.warning(f"Upload error for {bucket}/{path}: {e}")
        return ""

def upload_photo(st_file, project_name: str, report_date: date) -> str:
    ts = int(datetime.utcnow().timestamp())
    path = f"{report_date.isoformat()}/{project_name.replace('/','-')}/{ts}_{st_file.name.replace(' ','_')}"
    return upload_bytes_to_bucket(BUCKET_PHOTOS, path, st_file.getvalue(), st_file.type or "application/octet-stream")

def upload_audio_bytes(project_name: str, report_date: date, wav_bytes: bytes) -> str:
    if not wav_bytes: return ""
    ts = int(datetime.utcnow().timestamp())
    path = f"{report_date.isoformat()}/{project_name.replace('/','-')}/{ts}_voice.wav"
    return upload_bytes_to_bucket(BUCKET_AUDIO, path, wav_bytes, "audio/wav")

# ---- LLM helpers ----
def transcribe_wav_bytes(wav_bytes: bytes) -> str:
    if SAFE_MODE or not llm_available or not wav_bytes: return ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(wav_bytes); tmp.flush()
            with open(tmp.name, "rb") as f:
                resp = openai_client.audio.transcriptions.create(model="whisper-1", file=f)
        return getattr(resp, "text", "") or ""
    except Exception as e:
        st.warning(f"Transcription failed (continuing): {e}")
        return ""

def _extract_output_text(resp) -> str:
    if hasattr(resp, "output_text"): return resp.output_text
    try: return resp.output[0].content[0].text
    except Exception: pass
    try: return resp.choices[0].message.content
    except Exception: pass
    return ""

def extract_structured_with_gpt(raw_text: str) -> dict:
    if SAFE_MODE or not llm_available or not raw_text.strip(): return {}
    schema = {
        "type": "object",
        "properties": {
            "crew_counts": {"type":"array","items":{"type":"object","properties":{"trade":{"type":"string"},"count":{"type":"number"}},"required":["trade","count"]}},
            "equipment":   {"type":"array","items":{"type":"object","properties":{"type":{"type":"string"},"count":{"type":"number"}},"required":["type","count"]}},
            "activities":  {"type":"array","items":{"type":"object","properties":{"location":{"type":"string"},"description":{"type":"string"}},"required":["description"]}},
            "quantities":  {"type":"array","items":{"type":"object","properties":{"item":{"type":"string"},"unit":{"type":"string"},"value":{"type":"number"}},"required":["item","value"]}},
            "safety": {"type":"string"},
            "issues_delays": {"type":"string"}
        },
        "required": []
    }
    system = "Normalize construction daily report notes into JSON matching the schema. Return ONLY JSON."
    user = f"Notes/transcript:\n\n{raw_text}\n\nExtract the fields; unknowns should be empty arrays/strings."
    try:
        resp = openai_client.responses.create(
            model="gpt-4o-mini",
            input=[{"role":"system","content":system}, {"role":"user","content":user}],
            response_format={"type":"json_schema","json_schema":{"name":"daily_report_structured","schema":schema,"strict":True}}
        )
        txt = _extract_output_text(resp)
        data = json.loads(txt)
        return {
            "crew_counts": data.get("crew_counts", []),
            "equipment": data.get("equipment", []),
            "activities": data.get("activities", []),
            "quantities": data.get("quantities", []),
            "safety": data.get("safety", ""),
            "issues_delays": data.get("issues_delays", ""),
        }
    except Exception as e:
        st.warning(f"LLM extraction failed (continuing): {e}")
        return {}

# =========================
# Recorder + auto-extract to form
# =========================
st.subheader("🎙️ Voice note (optional)")
st.caption("Tap to record, speak, tap again to stop. The form will auto-fill from the transcript; review and edit before saving.")

recorded_bytes = None
if audiorec_available and not SAFE_MODE:
    try:
        recorded_bytes = st_audiorec()
    except Exception as e:
        st.warning(f"Recorder temporarily unavailable: {e}")
        recorded_bytes = None
else:
    st.info("Mic is disabled in Safe-Boot. Add `?safe=0` to enable.")

# Handle recorder output robustly
if recorded_bytes:
    new_hash = md5_bytes(recorded_bytes)
    if st.session_state.get("skip_record_once"):
        st.session_state["skip_record_once"] = False
    elif new_hash != st.session_state.get("audio_hash"):
        st.session_state["recorded_audio"] = recorded_bytes
        st.session_state["audio_hash"] = new_hash

        # 1) Transcribe
        transcript = transcribe_wav_bytes(recorded_bytes) or ""
        st.session_state["transcript_prefill"] = transcript

        # 2) Auto-extract from transcript to prefill the form
        extracted = extract_structured_with_gpt(transcript) if transcript.strip() else {}

        st.session_state["crew_prefill"]   = crew_to_text(extracted.get("crew_counts", []))
        st.session_state["equip_prefill"]  = equip_to_text(extracted.get("equipment", []))
        st.session_state["acts_prefill"]   = acts_to_text(extracted.get("activities", []))
        st.session_state["qty_prefill"]    = qtys_to_text(extracted.get("quantities", []))
        st.session_state["safety_prefill"] = extracted.get("safety", "")
        st.session_state["issues_prefill"] = extracted.get("issues_delays", "")

        # 3) Rebuild widgets with new values
        st.session_state["nonce"] += 1
        st.rerun()

cols = st.columns([1,1,3])
with cols[0]:
    if st.session_state["recorded_audio"]:
        st.audio(st.session_state["recorded_audio"], format="audio/wav")
with cols[1]:
    if st.session_state["recorded_audio"]:
        if st.button("Clear recording"):
            reset_all_fields()
            st.success("Recording cleared.")
            st.stop()  # end this run cleanly

# Transcript box (always editable)
transcript_text = st.text_area(
    "Transcribed audio (editable)",
    value=st.session_state.get("transcript_prefill", ""),
    key=f"transcript_{st.session_state['nonce']}",
    height=120,
    placeholder="Transcript appears here after recording…",
)

st.divider()
use_llm = st.checkbox(
    "🔧 Auto-structure Notes + Transcript with GPT on submit",
    value=(not SAFE_MODE),
    help="If on, GPT also parses notes+transcript when saving (fills any missing fields)."
)

# =========================
# Form (prefilled, editable)
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
    crew_text = st.text_area(
        "e.g., Carpenters: 6, Ironworkers: 4",
        value=st.session_state.get("crew_prefill",""),
        key=f"crew_{st.session_state['nonce']}",
        height=60
    )

    st.markdown("**Equipment** *(Type:Count, Type:Count)*")
    equip_text = st.text_area(
        "e.g., Excavator: 2, Telehandler: 1",
        value=st.session_state.get("equip_prefill",""),
        key=f"equip_{st.session_state['nonce']}",
        height=60
    )

    st.markdown("**Activities (free text or bullets)**")
    activities_text = st.text_area(
        "e.g., Formed footings at Grid A; Poured slab at Area 3",
        value=st.session_state.get("acts_prefill",""),
        key=f"acts_{st.session_state['nonce']}",
        height=90
    )

    st.markdown("**Quantities** *(one per line: Item [Unit]: Value)*")
    quantities_text = st.text_area(
        "e.g., Concrete CY: 35\nLF curb: 120",
        value=st.session_state.get("qty_prefill",""),
        key=f"qty_{st.session_state['nonce']}",
        height=80
    )

    subs_text = st.text_input("Subcontractors present (comma-separated)", placeholder="ACME Paving, XYZ Steel")

    safety_text = st.text_area(
        "Safety observations",
        value=st.session_state.get("safety_prefill",""),
        key=f"safety_{st.session_state['nonce']}",
        height=80
    )

    issues_text = st.text_area(
        "Issues / delays",
        value=st.session_state.get("issues_prefill",""),
        key=f"issues_{st.session_state['nonce']}",
        height=80
    )

    photos = st.file_uploader(
        "Photos", type=["jpg","jpeg","png"], accept_multiple_files=True,
        key=f"photos_{st.session_state['nonce']}",
    )

    notes_raw = st.text_area("Raw notes (optional)", placeholder="Paste any raw notes here", height=80)

    submitted = st.form_submit_button("Submit report")

# =========================
# Submit handler
# =========================
def upload_photo_safe(p, project, report_date):
    try:
        return upload_photo(p, project, report_date)
    except Exception as e:
        st.warning(f"Photo upload failed for {p.name}: {e}")
        return None

if submitted:
    try:
        with st.spinner("Structuring (if enabled), uploading media, and saving report..."):
            # Upload audio (even if transcript empty)
            audio_url = upload_audio_bytes(project, report_date, st.session_state.get("recorded_audio"))

            # Upload photos
            photo_urls = []
            if photos:
                for p in photos:
                    url = upload_photo_safe(p, project, report_date)
                    if url: photo_urls.append(url)

            # LLM extraction on submit (notes + transcript) to fill any missing fields
            combined_text = ""
            if notes_raw and notes_raw.strip(): combined_text += notes_raw.strip() + "\n\n"
            if transcript_text and transcript_text.strip(): combined_text += "Transcript:\n" + transcript_text.strip()

            extracted = {}
            if use_llm and combined_text.strip():
                extracted = extract_structured_with_gpt(combined_text)

            # Merge manual inputs with extracted (manual wins)
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
            subs_present      = str_to_list(subs_text)

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

            if not supabase:
                st.error("Supabase not initialized; cannot save row.")
            else:
                supabase.table("daily_reports").insert(row).execute()
                st.success("✅ Report saved.")
                reset_all_fields()
                st.stop()

    except Exception as e:
        st.error(f"App error: {e}")

# =========================
# Diagnostics
# =========================
with st.expander("App health / diagnostics"):
    def diag(msg): st.write("• " + msg)
    diag(f"SAFE_MODE: {SAFE_MODE}")
    diag(f"Supabase client: {'OK' if supabase else 'NOT READY'}")
    diag(f"OpenAI client: {'OK' if (openai_client and llm_available and not SAFE_MODE) else 'disabled'}")
    diag(f"Recorder available: {audiorec_available and not SAFE_MODE}")
    diag(f"Secrets present: {all(k in st.secrets for k in ['SUPABASE_URL','SUPABASE_KEY'])} (Supabase), {'OPENAI_API_KEY' in st.secrets} (OpenAI)")
    diag(f"Buckets: photos='{BUCKET_PHOTOS}', audio='{BUCKET_AUDIO}'")