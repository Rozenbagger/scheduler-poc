import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
import os
from ortools.sat.python import cp_model

# --- 1. SETTINGS & PERSISTENCE ---
st.set_page_config(page_title="Shift Command v25", page_icon="🏥", layout="wide")

DB_FILE = "local_database.json"

def load_data():
    default_state = {
        "global_unavail": [], 
        "saved_schedule": None, 
        "start_date": str(datetime.date.today()), 
        "num_days": 30,
        "admin_rules": []
    }
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                # Ensure all default keys exist
                for k, v in default_state.items():
                    if k not in data: data[k] = v
                return data
        except: return default_state
    return default_state

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

if "db_state" not in st.session_state:
    st.session_state.db_state = load_data()

# Dataframe state for UI tables
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

# --- 2. THE AI REASONING ENGINE (Updated for SDK 2026) ---
def call_ai_logic(prompt, key, is_json=True):
    client = genai.Client(api_key=key)
    config = {"response_mime_type": "application/json"} if is_json else {}
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=config
    )
    return json.loads(response.text) if is_json else response.text

# --- 3. ADMIN DASHBOARD ---
def admin_view():
    with st.sidebar:
        st.header("🔑 Authentication")
        api_key = st.text_input("Gemini API Key:", type="password")
        st.divider()
        st.info("System is running on Gemini 2.5 Flash for high-reasoning logic.")

    st.title("Admin Dashboard")
    tab_data, tab_rules, tab_calendar = st.tabs(["📊 Shift Management", "⚖️ Rule Ledger", "🗓️ Calendar"])

    # --- TAB 1: DATA MANAGEMENT ---
    with tab_data:
        st.subheader("Shift & Roster Definitions")
        
        # Direct Input Field for Data Changes
        data_input = st.text_input("AI Data Command", placeholder="e.g. 'Add a Swing shift from 15:00 to 23:00 with 1 headcount'")
        if st.button("Apply Data Update") and api_key:
            prompt = f"""
            Extract shift or physician additions from this text: '{data_input}'.
            Return JSON object with 'updates' array. 
            Example update: {{"type": "add_shift", "Shift Name": "Swing", "Start Time": "15:00", "End Time": "23:00", "Req Headcount": 1}}
            """
            try:
                res = call_ai_logic(prompt, api_key)
                for item in res.get("updates", []):
                    if item["type"] == "add_shift":
                        new_row = {
                            "Task ID": f"TSK-{len(st.session_state.shifts_df)+1:02d}",
                            "Shift Name": item["Shift Name"],
                            "Start Time": item["Start Time"],
                            "End Time": item["End Time"],
                            "Req Headcount": int(item["Req Headcount"])
                        }
                        st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, pd.DataFrame([new_row])], ignore_index=True)
                st.success("Shift added successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"AI could not parse command: {e}")

        c1, c2 = st.columns(2)
        with c1:
            st.write("**Defined Shifts**")
            st.session_state.shifts_df = st.data_editor(st.session_state.shifts_df, hide_index=True, num_rows="dynamic")
        with c2:
            st.write("**Provider Roster**")
            st.session_state.physicians_df = st.data_editor(st.session_state.physicians_df, hide_index=True, num_rows="dynamic")

    # --- TAB 2: RULE LEDGER ---
    with tab_rules:
        st.subheader("Active Scheduling Rules")
        
        # Direct Input Field for Rules
        rule_input = st.text_input("AI Rule Command", placeholder="e.g. 'Dr. Smith avoids Night Shift'")
        if st.button("Add to Ledger") and api_key:
            roster = list(st.session_state.physicians_df["Name"])
            shifts = list(st.session_state.shifts_df["Shift Name"])
            prompt = f"""
            Translate this rule: '{rule_input}'. 
            Roster: {roster}. Shifts: {shifts}.
            Return JSON array: [{{"physician_name": "...", "constraint_type": "soft_avoid_shift", "target_shift": "..."}}]
            """
            try:
                parsed = call_ai_logic(prompt, api_key)
                for p in parsed:
                    st.session_state.db_state["admin_rules"].append({
                        "active": True, 
                        "description": rule_input, 
                        "logic": p
                    })
                save_data(st.session_state.db_state)
                st.rerun()
            except: st.error("Rule could not be parsed.")

        # The Ledger List
        if st.session_state.db_state["admin_rules"]:
            rules_df = pd.DataFrame(st.session_state.db_state["admin_rules"])
            edited_rules = st.data_editor(rules_df[["active", "description"]], hide_index=True, use_container_width=True)
            
            if st.button("Save Ledger Changes"):
                for i, row in edited_rules.iterrows():
                    st.session_state.db_state["admin_rules"][i]["active"] = row["active"]
                save_data(st.session_state.db_state)
                st.toast("Rules updated.")

    # --- TAB 3: CALENDAR (Solver logic placeholder) ---
    with tab_calendar:
        if st.button("🚀 Generate Optimized Schedule"):
            st.warning("Solver calculations would run here using current Rules and Data.")

# Start App
admin_view()
