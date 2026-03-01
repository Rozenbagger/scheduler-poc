import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
from ortools.sat.python import cp_model

# --- 1. SETTINGS & UI CONFIG ---
st.set_page_config(page_title="Medical Scheduler v17.1", page_icon="🏥", layout="wide")

# Initialize Session State for Dataframes
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

# Force-refresh key for data editors
if "widget_sync" not in st.session_state:
    st.session_state.widget_sync = 0

# --- 2. SIDEBAR TOOLS (RESTORED) ---
with st.sidebar:
    st.header("📅 Timeline & Constraints")
    
    # 1. Calendar Start Tool
    start_date = st.date_input("Schedule Start Date", datetime.date.today())
    
    # 2. Duration Tool
    num_days = st.slider("Schedule Duration (Days)", 7, 60, 30)
    
    # 3. Rest Days/Hours Tool
    min_rest = st.number_input("Min. Rest Hours between shifts", 0, 48, 12)
    
    st.divider()
    api_key = st.text_input("Gemini API Key:", type="password")
    
    if st.button("Reset Session"):
        st.session_state.clear()
        st.rerun()

# --- 3. AI CORE (GEMINI 2.5 FLASH) ---
def run_ai_command(user_input, api_key):
    client = genai.Client(api_key=api_key)
    prompt = f"""
    Update the schedule data based on: "{user_input}"
    Return ONLY a JSON object:
    {{
      "action": "add_shift" OR "add_physician",
      "data": {{ 
        "Shift Name": "...", "Start": "HH:MM", "End": "HH:MM", "Count": 1,
        "Name": "...", "Max Shifts": 20
      }}
    }}
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)

# --- 4. ADMIN INTERFACE ---
st.title("🏥 Medical Scheduler")

# AI COMMAND BAR
st.subheader("🤖 AI Data Entry")
cmd_text = st.text_input("Add a shift or physician:", placeholder="e.g. 'Add Dr. Patel with max 15 shifts'")

if st.button("Execute") and api_key:
    try:
        result = run_ai_command(cmd_text, api_key)
        data = result.get("data", {})
        
        if result["action"] == "add_shift":
            new_row = pd.DataFrame([data])
            st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, new_row], ignore_index=True)
        
        elif result["action"] == "add_physician":
            new_row = pd.DataFrame([data])
            st.session_state.physicians_df = pd.concat([st.session_state.physicians_df, new_row], ignore_index=True)
            
        st.session_state.widget_sync += 1
        st.rerun()
    except Exception as e:
        st.error(f"AI Error: {e}")

# DATA TABLES
col1, col2 = st.columns(2)
with col1:
    st.write("### 🕒 Shifts")
    st.session_state.shifts_df = st.data_editor(st.session_state.shifts_df, key=f"s_{st.session_state.widget_sync}", hide_index=True, num_rows="dynamic")

with col2:
    st.write("### 👨‍⚕️ Physicians")
    st.session_state.physicians_df = st.data_editor(st.session_state.physicians_df, key=f"p_{st.session_state.widget_sync}", hide_index=True, num_rows="dynamic")

# --- 5. GENERATION ---
if st.button("🚀 Generate Schedule for " + str(num_days) + " Days", type="primary"):
    st.success(f"Solver configured for {start_date} with {min_rest}h rest periods.")
