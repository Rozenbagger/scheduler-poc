import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
import os

# --- PERSISTENCE ---
DB_FILE = "scheduler_db.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {"shifts": [], "physicians": [], "rules": []}

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=4)

# Initialize Session
if "db" not in st.session_state:
    st.session_state.db = load_db()

# --- AI CORE ---
def ai_command(prompt, api_key):
    client = genai.Client(api_key=api_key)
    # Using Gemini 3 Flash for the v20-style high-speed extraction
    response = client.models.generate_content(
        model='gemini-3-flash',
        contents=prompt,
        config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

# --- UI ---
st.title("🏥 Medical Scheduler (v20 Restored)")

with st.sidebar:
    api_key = st.text_input("Gemini API Key", type="password")
    if st.button("Clear All Data"):
        st.session_state.db = {"shifts": [], "physicians": [], "rules": []}
        save_db(st.session_state.db)
        st.rerun()

tab1, tab2, tab3 = st.tabs(["🕒 Shifts", "👨‍⚕️ Physicians", "📜 Rules"])

# TAB 1: SHIFTS
with tab1:
    st.subheader("Shift Management")
    shift_cmd = st.text_input("Add Shift (e.g., 'Day Shift 07:00-15:00 headcount 2')")
    if st.button("Add Shift") and api_key:
        p = f"Extract shift: '{shift_cmd}'. Return JSON: {{'name': '...', 'start': 'HH:MM', 'end': 'HH:MM', 'count': 1}}"
        res = ai_command(p, api_key)
        st.session_state.db["shifts"].append(res)
        save_db(st.session_state.db)
        st.success(f"Added {res['name']}")

    st.table(pd.DataFrame(st.session_state.db["shifts"]))

# TAB 2: PHYSICIANS 
with tab2:
    st.subheader("Physician Roster")
    phys_cmd = st.text_input("Add Physician (e.g., 'Dr. Smith, max 40 hours')")
    if st.button("Add Physician") and api_key:
        p = f"Extract physician: '{phys_cmd}'. Return JSON: {{'name': '...', 'max_hours': 40}}"
        res = ai_command(p, api_key)
        st.session_state.db["physicians"].append(res)
        save_db(st.session_state.db)
        st.success(f"Added {res['name']}")
    
    st.table(pd.DataFrame(st.session_state.db["physicians"]))

# TAB 3: RULES (DISTINCT FROM DATA)
with tab3:
    st.subheader("Assignment Rules")
    rule_cmd = st.text_input("Add Rule (e.g., 'Dr. Jones avoids Night Shift')")
    if st.button("Save Rule") and api_key:
        # Rules are saved as plain text descriptions for the solver to read later
        st.session_state.db["rules"].append({"rule": rule_cmd, "active": True})
        save_db(st.session_state.db)
        st.toast("Rule recorded")

    st.data_editor(pd.DataFrame(st.session_state.db["rules"]))
