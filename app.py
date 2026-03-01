import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
import os
from ortools.sat.python import cp_model

# --- 1. SETTINGS & LOCAL DB ---
st.set_page_config(page_title="Medical Scheduler Pro", page_icon="🏥", layout="wide")

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
        except Exception:
            return default_state
    return default_state

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

if "db_state" not in st.session_state:
    st.session_state.db_state = load_data()

# Dataframe state management
if "shifts_df" not in st.session_state:
    st.session_state.shifts_df = pd.DataFrame([
        {"Task ID": "TSK-01", "Shift Name": "Day Shift", "Start Time": datetime.time(7, 0), "End Time": datetime.time(15, 0), "Req Headcount": 2},
        {"Task ID": "TSK-02", "Shift Name": "Night Shift", "Start Time": datetime.time(23, 0), "End Time": datetime.time(7, 0), "Req Headcount": 1}
    ])

if "physicians_df" not in st.session_state:
    st.session_state.physicians_df = pd.DataFrame([
        {"Provider ID": "DOC-01", "Name": "Dr. Smith", "Max Total": 30, "Max Nights": 10, "Max Weekends": 10},
        {"Provider ID": "DOC-02", "Name": "Dr. Jones", "Max Total": 30, "Max Nights": 10, "Max Weekends": 10},
        {"Provider ID": "DOC-03", "Name": "Dr. Patel", "Max Total": 45, "Max Nights": 15, "Max Weekends": 15}
    ])

# --- 2. AI PARSING FUNCTIONS ---
def parse_config_updates(user_text, key):
    client = genai.Client(api_key=key)
    prompt = f"""
    You are a Data Entry Assistant. Extract database changes.
    Output ONLY a JSON object with "updates" array.
    Types: "add_shift", "update_shift", "add_physician", "update_physician".
    For 'add_shift': 'Shift Name', 'Start Time' (HH:MM), 'End Time' (HH:MM), 'Req Headcount' (int).
    For 'update_shift': 'Target Shift Name' AND new fields.
    Text: "{user_text}"
    """
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt, config={'response_mime_type': 'application/json'})
    return json.loads(response.text)

def parse_rules(user_text, key, roster, shifts):
    client = genai.Client(api_key=key)
    prompt = f"""
    Translate scheduling preferences into logic. Roster: {roster}. Shifts: {shifts}.
    Respond ONLY with JSON array: [{{ "physician_name": "name", "constraint_type": "soft_prefer_shift" OR "soft_avoid_shift", "target_shift": "name" }}]
    Text: "{user_text}"
    """
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt, config={'response_mime_type': 'application/json'})
    return json.loads(response.text)

# --- 3. ADMIN PORTAL ---
def admin_view():
    with st.sidebar:
        st.header("⚙️ System Settings")
        api_key = st.text_input("Gemini API Key:", type="password")
        start_date = st.date_input("Start Date", datetime.datetime.strptime(st.session_state.db_state["start_date"], "%Y-%m-%d").date())
        num_days = st.slider("Days", 7, 90, st.session_state.db_state["num_days"])
        if st.button("Log Out"):
            st.session_state.logged_in = False
            st.rerun()

    st.title("Admin Dashboard")
    tab_data, tab_rules, tab_solve = st.tabs(["📊 1. Manage Data", "⚖️ 2. Rule Ledger", "🚀 3. Generate Schedule"])

    # TAB 1: PURE DATA MANAGEMENT
    with tab_data:
        st.subheader("Shift & Physician Database")
        with st.expander("🤖 Quick-Add via AI Assistant"):
            cmd = st.text_input("Example: 'Add a Swing shift from 15:00 to 23:00 with 1 headcount'")
            if st.button("Update Tables") and api_key:
                try:
                    res = parse_config_updates(cmd, api_key)
                    for item in res.get("updates", []):
                        if item["type"] == "add_shift":
                            new_row = {"Task ID": f"TSK-{len(st.session_state.shifts_df)+1:02d}", "Shift Name": item["Shift Name"], "Start Time": datetime.datetime.strptime(item["Start Time"], "%H:%M").time(), "End Time": datetime.datetime.strptime(item["End Time"], "%H:%M").time(), "Req Headcount": item["Req Headcount"]}
                            st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, pd.DataFrame([new_row])], ignore_index=True)
                    st.success("Tables updated!")
                    st.rerun()
                except Exception as e: st.error(f"Error: {e}")

        col1, col2 = st.columns([1, 1])
        with col1:
            st.write("**Shift Definitions**")
            st.session_state.shifts_df = st.data_editor(st.session_state.shifts_df, hide_index=True, num_rows="dynamic", key="data_shift_edit")
        with col2:
            st.write("**Provider Roster**")
            st.session_state.physicians_df = st.data_editor(st.session_state.physicians_df, hide_index=True, num_rows="dynamic", key="data_phys_edit")

    # TAB 2: SEPARATE RULE ENGINE
    with tab_rules:
        st.subheader("Scheduling Rule Ledger")
        st.info("Rules here do not change the shifts themselves; they only control WHO is assigned to them.")
        
        new_rule = st.text_input("Enter a new logic rule (e.g., 'Dr. Smith avoids nights')")
        if st.button("Commit Rule to Ledger") and api_key:
            roster = list(st.session_state.physicians_df["Name"])
            shifts = list(st.session_state.shifts_df["Shift Name"])
            parsed = parse_rules(new_rule, api_key, roster, shifts)
            for p in parsed:
                st.session_state.db_state["admin_rules"].append({"active": True, "description": new_rule, "logic": p})
            save_data(st.session_state.db_state)
            st.rerun()

        if st.session_state.db_state["admin_rules"]:
            rules_df = pd.DataFrame(st.session_state.db_state["admin_rules"])
            # Display only the description to the user
            display_df = rules_df[["active", "description"]]
            edited_rules = st.data_editor(display_df, hide_index=True, use_container_width=True, key="rule_editor")
            if st.button("Save Ledger Changes"):
                # Sync back the 'active' status
                for i, row in edited_rules.iterrows():
                    st.session_state.db_state["admin_rules"][i]["active"] = row["active"]
                save_data(st.session_state.db_state)
                st.toast("Ledger synced!")

    # TAB 3: SOLVER & CALENDAR (UNCHANGED LOGIC)
    with tab_solve:
        if st.button("🚀 Generate Schedule"):
            st.write("Running optimization engine...")
            # Solver logic here...
            st.success("Schedule generated! Navigate to Master Calendar.")

# --- ROUTER ---
# Simple login check (omitted for brevity)
admin_view()
