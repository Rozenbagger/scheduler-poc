import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
import os
from ortools.sat.python import cp_model

# --- 1. SETTINGS & LOCAL DB ---
st.set_page_config(page_title="Medical Scheduler Pro", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

DB_FILE = "local_database.json"

def load_data():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"global_unavail": [], "saved_schedule": None}
    return {"global_unavail": [], "saved_schedule": None}

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Initialize Session State
if "db_state" not in st.session_state:
    st.session_state.db_state = load_data()

if "shifts_df" not in st.session_state:
    st.session_state.shifts_df = pd.DataFrame([
        {"Task ID": "TSK-01", "Shift Name": "Day Shift", "Start Time": "07:00", "End Time": "15:00", "Req Headcount": 2},
        {"Task ID": "TSK-02", "Shift Name": "Night Shift", "Start Time": "23:00", "End Time": "07:00", "Req Headcount": 1}
    ])

if "physicians_df" not in st.session_state:
    st.session_state.physicians_df = pd.DataFrame([
        {"Provider ID": "DOC-01", "Name": "Dr. Smith", "Max Total": 30, "Max Nights": 10, "Max Weekends": 10},
        {"Provider ID": "DOC-02", "Name": "Dr. Jones", "Max Total": 30, "Max Nights": 10, "Max Weekends": 10}
    ])

# Login State
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_role = None
    st.session_state.current_name = None

# --- 2. HELPER FUNCTIONS ---
def safe_int(val, default=0):
    try:
        if pd.isna(val) or val is None or str(val).strip() == "": return default
        return int(float(val))
    except (ValueError, TypeError): return default

def parse_constraints(user_text, key, roster, shifts):
    client = genai.Client(api_key=key)
    prompt = f"Extract rules. Roster: {roster}. Shifts: {shifts}. Return JSON array: [{{ 'physician_name': '...', 'constraint_type': 'hard_time_off', 'target_day': int, 'target_shift': '...' }}]. Text: '{user_text}'"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt, config={'response_mime_type': 'application/json'})
    return json.loads(response.text)

def parse_config_updates(user_text, key):
    client = genai.Client(api_key=key)
    prompt = f"Extract additions. JSON 'updates' array of 'type': 'add_shift' or 'add_physician'. Include Name, Start (HH:MM), End (HH:MM), Count (int) or Max Total (int). Text: '{user_text}'"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt, config={'response_mime_type': 'application/json'})
    return json.loads(response.text)

# --- 3. VIEWS ---
def login_screen():
    st.title("🏥 Shift Command")
    with st.form("login"):
        u = st.text_input("Username").lower()
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            if u == "admin" and p == "admin":
                st.session_state.logged_in, st.session_state.current_role = True, "Admin"
                st.rerun()
            else: st.error("Invalid credentials.")

def admin_view():
    with st.sidebar:
        st.header("⚙️ Global Config")
        api_key = st.text_input("Gemini API Key", type="password")
        num_days = st.slider("Duration (Days)", 7, 31, 14)
        solver_timeout = st.slider("Solver Timeout (s)", 10, 120, 30)
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.rerun()

    st.title("Admin Dashboard")
    t1, t2, t3 = st.tabs(["👥 Data", "🧠 Engine", "🗓️ Calendar"])

    with t1:
        st.subheader("AI Update")
        ai_cmd = st.text_input("Add a doctor or shift via text:")
        if st.button("Execute AI Command") and api_key:
            res = parse_config_updates(ai_cmd, api_key)
            for item in res.get("updates", []):
                if item["type"] == "add_shift":
                    st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, pd.DataFrame([{"Task ID": f"TSK-{len(st.session_state.shifts_df)+1}", "Shift Name": item.get("Shift Name"), "Start Time": item.get("Start Time"), "End Time": item.get("End Time"), "Req Headcount": item.get("Req Headcount", 1)}])], ignore_index=True)
                elif item["type"] == "add_physician":
                    st.session_state.physicians_df = pd.concat([st.session_state.physicians_df, pd.DataFrame([{"Provider ID": f"DOC-{len(st.session_state.physicians_df)+1}", "Name": item.get("Name"), "Max Total": item.get("Max Total", 20)}])], ignore_index=True)
            st.rerun()
        
        c_a, c_b = st.columns(2)
        with c_a: st.session_state.shifts_df = st.data_editor(st.session_state.shifts_df, key="s_ed", hide_index=True)
        with c_b: st.session_state.physicians_df = st.data_editor(st.session_state.physicians_df, key="p_ed", hide_index=True)

    with t2:
        st.subheader("Solver Constraints")
        user_req = st.text_area("Custom AI Rules:", "Dr. Smith avoids Night Shift.")
        if st.button("🚀 Run Solver", type="primary") and api_key:
            with st.spinner("Calculating..."):
                # Setup OR-Tools
                model = cp_model.CpModel()
                shifts = st.session_state.shifts_df.to_dict('records')
                physicians = st.session_state.physicians_df.to_dict('records')
                phys_names = [p['Name'] for p in physicians] + ["UNASSIGNED"]
                
                # Variables
                assign = {}
                for d in range(num_days):
                    for s_idx, s in enumerate(shifts):
                        for p in phys_names:
                            assign[(d, s_idx, p)] = model.NewBoolVar(f'd{d}s{s_idx}p{p}')
                
                # Hard Rules
                for d in range(num_days):
                    for s_idx, s in enumerate(shifts):
                        model.Add(sum(assign[(d, s_idx, p)] for p in phys_names) == int(s['Req Headcount']))

                for p in [p for p in physicians if p['Name'] != "UNASSIGNED"]:
                    model.Add(sum(assign[(d, s_idx, p['Name'])] for d in range(num_days) for s_idx in range(len(shifts))) <= int(p['Max Total']))

                # Minimize Unassigned
                model.Minimize(sum(assign[(d, s_idx, "UNASSIGNED")] for d in range(num_days) for s_idx in range(len(shifts))))
                
                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = solver_timeout
                status = solver.Solve(model)

                if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                    results = []
                    for d in range(num_days):
                        row = {"Day": d+1}
                        for s_idx, s in enumerate(shifts):
                            row[s['Shift Name']] = ", ".join([p for p in phys_names if solver.Value(assign[(d, s_idx, p)])])
                        results.append(row)
                    st.session_state.db_state["saved_schedule"] = results
                    save_data(st.session_state.db_state)
                    st.success("Solve Complete!")
                else: st.error("Infeasible constraints.")

    with t3:
        if st.session_state.db_state.get("saved_schedule"):
            df = pd.DataFrame(st.session_state.db_state["saved_schedule"])
            st.dataframe(df.style.applymap(lambda v: 'color: red' if "UNASSIGNED" in str(v) else ''), use_container_width=True, hide_index=True)
        else: st.info("No schedule generated yet.")

# --- 4. MAIN ENTRY ---
if __name__ == "__main__":
    if not st.session_state.logged_in:
        login_screen()
    else:
        admin_view()
