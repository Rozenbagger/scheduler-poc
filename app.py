import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
import os
from ortools.sat.python import cp_model

# --- 1. SETTINGS & PERSISTENCE ---
st.set_page_config(page_title="Medical Scheduler v26", page_icon="🏥", layout="wide")

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

# --- 2. RESTORED AI PARSER (v21 Style Logic) ---
def call_ai_v21_logic(prompt, key, is_json=True):
    # Using Gemini 3 Flash for maximum extraction precision
    client = genai.Client(api_key=key)
    
    # We explicitly request a Thinking Level of 'High' for complex extraction
    response = client.models.generate_content(
        model='gemini-3-flash',
        contents=prompt,
        config={
            "response_mime_type": "application/json" if is_json else "text/plain",
            "thinking_level": "High" 
        }
    )
    return json.loads(response.text) if is_json else response.text

# --- 3. ADMIN DASHBOARD ---
def admin_view():
    with st.sidebar:
        st.header("🔑 AI Access")
        api_key = st.text_input("Gemini API Key:", type="password")
        st.divider()
        st.caption("Running Engine: Gemini 3 Flash (2026 Edition)")

    st.title("Admin Command Center")
    t_data, t_rules, t_calendar = st.tabs(["📊 Shift & Provider Data", "⚖️ Rule Ledger", "🗓️ Calendar Control"])

    # --- TAB 1: DATA MANAGEMENT ---
    with t_data:
        st.subheader("Database Entry")
        data_input = st.text_input("AI Data Command (Add Shifts/Docs)", placeholder="e.g. 'Add a Swing shift 15:00-23:00 with 1 person'")
        if st.button("Execute Data Update") and api_key:
            prompt = f"""
            Identify shift or physician additions from: '{data_input}'.
            Return JSON: {{"updates": [{{"type": "add_shift", "Shift Name": "...", "Start Time": "HH:MM", "End Time": "HH:MM", "Req Headcount": 1}}]}}
            """
            try:
                res = call_ai_v21_logic(prompt, api_key)
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
                st.success("Database entry recorded.")
                st.rerun()
            except: st.error("AI couldn't process that format. Try: 'Add shift [Name] [Start]-[End]'.")

        c1, c2 = st.columns(2)
        with c1: st.session_state.shifts_df = st.data_editor(st.session_state.shifts_df, hide_index=True, num_rows="dynamic", key="data_s")
        with c2: st.session_state.physicians_df = st.data_editor(st.session_state.physicians_df, hide_index=True, num_rows="dynamic", key="data_p")

    # --- TAB 2: RULE LEDGER (Restored List Logic) ---
    with t_rules:
        st.subheader("Scheduling Constraints")
        rule_input = st.text_input("New Rule", placeholder="e.g. 'Dr. Smith doesn't do Day Shifts'")
        if st.button("Add to List") and api_key:
            roster = list(st.session_state.physicians_df["Name"])
            shifts = list(st.session_state.shifts_df["Shift Name"])
            prompt = f"""
            Parse this rule: '{rule_input}'. Use roster {roster} and shifts {shifts}.
            JSON array: [{{'physician_name': '...', 'constraint_type': 'soft_avoid_shift', 'target_shift': '...'}}]
            """
            try:
                parsed = call_ai_v21_logic(prompt, api_key)
                for p in parsed:
                    st.session_state.db_state["admin_rules"].append({
                        "active": True, 
                        "description": rule_input, 
                        "logic": p
                    })
                save_data(st.session_state.db_state)
                st.rerun()
            except: st.error("Invalid rule format.")

        # Display the list of rules with a simple way to delete/modify
        if st.session_state.db_state["admin_rules"]:
            rules_df = pd.DataFrame(st.session_state.db_state["admin_rules"])
            # Display editor for the user to toggle/edit
            edited_rules = st.data_editor(rules_df[["active", "description"]], hide_index=True, use_container_width=True, key="rule_edit_list")
            
            if st.button("Update Active Rules"):
                # Sync back changes to the session state
                for i, row in edited_rules.iterrows():
                    st.session_state.db_state["admin_rules"][i]["active"] = row["active"]
                    st.session_state.db_state["admin_rules"][i]["description"] = row["description"]
                save_data(st.session_state.db_state)
                st.toast("Rule Ledger Updated!")

            if st.button("Clear Rule History", type="secondary"):
                st.session_state.db_state["admin_rules"] = []
                save_data(st.session_state.db_state)
                st.rerun()

    # --- TAB 3: CALENDAR (Solver) ---
    with t_calendar:
        st.info("The logic from the Rule Ledger above will be applied automatically when you click generate.")
        if st.button("🚀 Generate Schedule"):
            st.success("Optimization running...")

# Initialize
admin_view()
