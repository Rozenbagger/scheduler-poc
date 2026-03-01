import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
import os

# --- 1. SETTINGS ---
st.set_page_config(page_title="Shift Command v20-Fixed", page_icon="🏥", layout="wide")

# Initialize Session State
if "shifts_df" not in st.session_state:
    st.session_state.shifts_df = pd.DataFrame([
        {"Task ID": "TSK-01", "Shift Name": "Day Shift", "Start Time": "07:00", "End Time": "15:00", "Req Headcount": 2},
        {"Task ID": "TSK-02", "Shift Name": "Night Shift", "Start Time": "23:00", "End Time": "07:00", "Req Headcount": 1}
    ])

if "physicians_df" not in st.session_state:
    st.session_state.physicians_df = pd.DataFrame([
        {"Provider ID": "DOC-01", "Name": "Dr. Smith", "Max Total": 30},
        {"Provider ID": "DOC-02", "Name": "Dr. Jones", "Max Total": 30}
    ])

# A counter to force the data_editor to refresh when AI updates the table
if "editor_key" not in st.session_state:
    st.session_state.editor_key = 0

# --- 2. THE AI ENGINE ---
def call_ai(prompt, key):
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)

# --- 3. ADMIN INTERFACE ---
st.title("🏥 Admin Dashboard")

with st.sidebar:
    api_key = st.text_input("Gemini API Key:", type="password")
    st.divider()
    if st.button("Reset All Data"):
        st.session_state.clear()
        st.rerun()

tab_config, tab_rules = st.tabs(["📊 Shift & Physician Data", "⚖️ Manual Rules"])

with tab_config:
    st.subheader("AI Table Commands")
    ai_input = st.text_input("Add/Update Data", placeholder="e.g. 'Add shift Swing 15:00-23:00 with 1 headcount'")
    
    if st.button("Run AI Command") and api_key:
        prompt = f"""
        Extract data updates from: '{ai_input}'. 
        Return JSON with 'updates' list. 
        Update Types: 'add_shift' (fields: Name, Start, End, Count) or 'add_physician' (fields: Name, Max).
        """
        try:
            res = call_ai(prompt, api_key)
            for item in res.get("updates", []):
                if item["type"] == "add_shift":
                    new_row = {
                        "Task ID": f"TSK-{len(st.session_state.shifts_df)+1:02d}",
                        "Shift Name": item.get("Name"),
                        "Start Time": item.get("Start"),
                        "End Time": item.get("End"),
                        "Req Headcount": int(item.get("Count", 1))
                    }
                    st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, pd.DataFrame([new_row])], ignore_index=True)
                
                elif item["type"] == "add_physician":
                    new_phys = {
                        "Provider ID": f"DOC-{len(st.session_state.physicians_df)+1:02d}",
                        "Name": item.get("Name"),
                        "Max Total": int(item.get("Max", 30))
                    }
                    st.session_state.physicians_df = pd.concat([st.session_state.physicians_df, pd.DataFrame([new_phys])], ignore_index=True)
            
            # Increment the key to force the widget to re-render with new data
            st.session_state.editor_key += 1
            st.success("Table updated successfully!")
            st.rerun()
        except Exception as e:
            st.error(f"AI could not parse command: {e}")

    c1, c2 = st.columns(2)
    with c1:
        st.write("**Shift Schedule**")
        # We use the dynamic key here to fix the "unable to update" bug
        st.session_state.shifts_df = st.data_editor(
            st.session_state.shifts_df, 
            key=f"shift_editor_{st.session_state.editor_key}", 
            hide_index=True, 
            num_rows="dynamic"
        )
    with c2:
        st.write("**Provider Roster**")
        st.session_state.physicians_df = st.data_editor(
            st.session_state.physicians_df, 
            key=f"phys_editor_{st.session_state.editor_key}", 
            hide_index=True, 
            num_rows="dynamic"
        )

with tab_rules:
    st.subheader("Assignment Rules")
    st.info("Manual rules go here. No persistent ledger logic applied.")
    rule_desc = st.text_area("Temporary Rule Notes (Non-persistent)")
