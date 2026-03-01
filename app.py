import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
from ortools.sat.python import cp_model

# --- 1. SETTINGS & UI CONFIG ---
st.set_page_config(page_title="Medical Scheduler v17", page_icon="🏥", layout="wide")

# Initialize Session State Dataframes
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

# --- 2. AI CORE (GEMINI 2.5 FLASH) ---
def run_ai_command(user_input, api_key):
    client = genai.Client(api_key=api_key)
    prompt = f"""
    You are a hospital admin assistant. Update the schedule data based on: "{user_input}"
    
    Return ONLY a JSON object with this structure:
    {{
      "action": "add_shift" OR "add_physician" OR "update",
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

# --- 3. ADMIN INTERFACE ---
st.title("🏥 Medical Scheduler v17")

with st.sidebar:
    api_key = st.text_input("Gemini API Key:", type="password")
    if st.button("Reset Session"):
        st.session_state.clear()
        st.rerun()

# AI COMMAND BAR
st.subheader("🤖 AI Command Center")
cmd_text = st.text_input("Tell the AI what to change:", placeholder="e.g. 'Add a Swing shift from 15:00 to 23:00 with headcount 1'")

if st.button("Execute Command") and api_key:
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
        st.success(f"Action '{result['action']}' completed!")
        st.rerun()
    except Exception as e:
        st.error(f"AI Error: {e}")

# DATA TABLES
st.divider()
col1, col2 = st.columns(2)

with col1:
    st.write("### 🕒 Shift Definitions")
    st.session_state.shifts_df = st.data_editor(
        st.session_state.shifts_df, 
        key=f"shift_ed_{st.session_state.widget_sync}",
        hide_index=True, 
        num_rows="dynamic"
    )

with col2:
    st.write("### 👨‍⚕️ Physician Roster")
    st.session_state.physicians_df = st.data_editor(
        st.session_state.physicians_df, 
        key=f"phys_ed_{st.session_state.widget_sync}",
        hide_index=True, 
        num_rows="dynamic"
    )

# --- 4. THE SOLVER (SIMPLIFIED) ---
if st.button("🚀 Generate Schedule", type="primary"):
    st.info("The OR-Tools solver is ready to process the tables above.")
