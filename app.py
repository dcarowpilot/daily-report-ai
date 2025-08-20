import streamlit as st
import os
from supabase import create_client

# Load secrets
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# Example: set OpenAI env var
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

# Example: connect to Supabase
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

st.title("Daily Report AI")
st.write("Hello, world! This is your starter appzz.")
