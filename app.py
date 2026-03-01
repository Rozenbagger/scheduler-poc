import streamlit as st
import pandas as pd
from google import genai
import json
import datetime

# --- 1. CORE SETTINGS ---
st.set_page_config(page_title="Medical Scheduler v14", page_icon="🏥", layout="wide")

# Initialize Session State (The "Live" Database)
if "shifts_df" not in st.session_state:
    st.session_state.shifts_df = pd.DataFrame([
        {"Shift Name": "Day Shift", "Start": "07:00", "End": "15:00", "Count": 2},
        {"Shift Name": "Night Shift", "Start": "23:00", "End": "07:00", "Count": 1}
    ])

if "physicians_df" not in st.session_state:
    st.session_state.physicians_df = pd.DataFrame([
        {"Name": "Dr. Smith", "Max Shifts": 20},
        {"Name": "Dr. Jones", "Max Shifts": 20}
    ])

# Helper to force-refresh the data editors
if "sync_key" not in st.session_state:
    st.session_state.sync_key = 0

# --- 2. THE SIDEBAR (CORE CAPABILITIES) ---
with st.sidebar:
    st.header("📅 Timeline Settings")
    
    # Restored Core Tools
    start_date = st.date_input("Schedule Start Date", datetime.date.today())
    num_days = st.slider("Total Days to Schedule", 7, 90, 30)
    min_rest = st.number_input("Min. Rest Hours", 0, 48, 12)
    
    st.divider()
    api_key = st.text_input("Gemini API Key:", type="password")
    
    if st.button("Factory Reset"):
        st.session_state.clear()
        st.rerun()

# --- 3. AI COMMAND LOGIC (v14 STYLE) ---
def process_ai_request(text, key):
    client = genai.Client(api_key=key)
    # The prompt is strictly limited to data extraction
    prompt = f"""
    Based on the input: "{text}", identify if the user wants to add a SHIFT or a PHYSICIAN.
    Return ONLY JSON:
    {{
      "target": "shift" or "physician",
      "payload": {{
        "Shift Name": "...", "Start": "HH:MM", "End": "HH:MM", "Count": 1,
        "Name": "...", "Max Shifts": 20
      }}
    }}
    """
    response = client.models.generate_content(
        model='gemini-2.0-flash', # Using 2.0 for v14-style speed/reliability
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)

# --- 4. MAIN INTERFACE ---
st.title("🏥 Medical Scheduler (v14)")

# The "Simple" Entry Field
st.markdown("### 🤖 Quick Add")
user_cmd = st.text_input("Enter command:", placeholder="e.g. 'Add Dr. Patel max 15 shifts' or 'Add Swing shift 15:00 to 23:00'")

if st.button("Apply to Tables") and api_key:
    try:
        res = process_ai_request(user_cmd, api_key)
        payload = res.get("payload", {})
        
        if res["target"] == "shift":
            new_row = pd.DataFrame([{k: payload.get(k) for k in ["Shift Name", "Start", "End", "Count"]}])
            st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, new_row], ignore_index=True)
        elif res["target"] == "physician":
            new_row = pd.DataFrame([{k: payload.get(k) for k in ["Name", "Max Shifts"]}])
            st.session_state.physicians_df = pd.concat([st.session_state.physicians_df, new_row], ignore_index=True)
        
        st.session_state.sync_key += 1 # Forces UI refresh
        st.success("Updated!")
        st.rerun()
    except Exception as e:
        st.error(f"Could not process: {e}")

# DISPLAY DATA
st.divider()
c1, c2 = st.columns(2)

with c1:
    st.subheader("🕒 Shifts")
    st.session_state.shifts_df = st.data_editor(
        st.session_state.shifts_df, 
        key=f"s_edit_{st.session_state.sync_key}", 
        hide_index=True, 
        num_rows="dynamic"
    )

with c2:
    st.subheader("👨‍⚕️ Physicians")
    st.session_state.physicians_df = st.data_editor(
        st.session_state.physicians_df, 
        key=f"p_edit_{st.session_state.sync_key}", 
        hide_index=True, 
        num_rows="dynamic"
    )

# --- 5. THE SOLVER BUTTON ---
st.divider()
if st.button("🚀 Calculate Optimal Schedule", type="primary"):
    st.write(f"Generating coverage from {start_date} for {num_days} days...")
