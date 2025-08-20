import streamlit as st
import os
from supabase import create_client


st.title("Daily Report AI – Env Test")

# Make sure you added these in Streamlit -> App -> Settings -> Secrets
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    st.write("✅ Secrets loaded.")
except Exception as e:
    st.error("❌ Could not load SUPABASE_URL / SUPABASE_KEY from Secrets.")
    st.stop()

# Try to import and connect
try:
    supabase = create_client(url, key)
    st.success("✅ Supabase client imported and initialized.")
except Exception as e:
    st.error(f"❌ Supabase init failed: {e}")