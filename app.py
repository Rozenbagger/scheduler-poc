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

if "db_state" not in st.session_state:
    st.session_state.db_state = load_data()

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

# --- 2. POC USER DATABASE ---
USER_DB = {
    "admin": {"password": "admin", "role": "Admin", "name": "System Administrator"},
    "drsmith": {"password": "test", "role": "Physician", "name": "Dr. Smith"},
    "drjones": {"password": "test", "role": "Physician", "name": "Dr. Jones"},
    "drpatel": {"password": "test", "role": "Physician", "name": "Dr. Patel"}
}

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_role = None
    st.session_state.current_name = None

# --- 3. HELPER FUNCTIONS ---
def safe_int(val, default=0):
    """Bulletproof function to prevent empty Streamlit cells from crashing the math."""
    try:
        if pd.isna(val) or val is None or str(val).strip() == "":
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default

def parse_constraints(user_text, key, roster, shifts):
    client = genai.Client(api_key=key)
    prompt = f"""
    You are a scheduling assistant. Extract scheduling rules. Roster: {roster}. Shifts: {shifts}.
    Respond ONLY with a JSON array: [{{ "physician_name": "exact name", "constraint_type": "soft_prefer_shift" OR "soft_avoid_shift" OR "hard_time_off", "target_day": integer or null, "target_shift": "exact shift name" or null }}]
    Text: "{user_text}"
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)

def parse_config_updates(user_text, key):
    client = genai.Client(api_key=key)
    prompt = f"""
    You are an AI database administrator. Extract table configuration additions.
    Output JSON with an "updates" array containing objects of "type": "add_shift" or "add_physician".
    For add_shift include: "Shift Name", "Start Time" (HH:MM string), "End Time" (HH:MM string), "Req Headcount" (integer).
    For add_physician include: "Name", "Max Total" (integer), "Max Nights" (integer), "Max Weekends" (integer).
    DO NOT generate IDs. The system will handle primary keys.
    Text: "{user_text}"
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)

def parse_timeoff_request(user_text, key):
    client = genai.Client(api_key=key)
    prompt = f"""
    Extract time-off requests.
    Output JSON array: [{{ "day": integer (e.g. 1 for Day 1), "start": "HH:MM", "end": "HH:MM" }}]
    Text: "{user_text}"
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)

def times_overlap(s_start, s_end, u_start, u_end):
    if pd.isna(s_start) or pd.isna(s_end) or pd.isna(u_start) or pd.isna(u_end): return False
    def to_mins(t): return t.hour * 60 + t.minute
    ss, se, us, ue = to_mins(s_start), to_mins(s_end), to_mins(u_start), to_mins(u_end)
    if se <= ss: se += 24 * 60
    if ue <= us: ue += 24 * 60
    return max(ss, us) < min(se, ue)

# --- 4. LOGIN SCREEN ---
def login_screen():
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.write("") 
        st.image("https://cdn-icons-png.flaticon.com/512/2966/2966327.png", width=80) 
        st.title("Shift Command")
        st.markdown("### Enterprise Medical Scheduling")
        
        with st.form("login_form", clear_on_submit=True):
            username = st.text_input("Username").lower()
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Authenticate", use_container_width=True):
                if username in USER_DB and USER_DB[username]["password"] == password:
                    st.session_state.logged_in = True
                    st.session_state.current_role = USER_DB[username]["role"]
                    st.session_state.current_name = USER_DB[username]["name"]
                    st.rerun()
                else:
                    st.error("Authentication failed. Check credentials.")

# --- 5. PHYSICIAN PORTAL ---
def physician_view():
    with st.sidebar:
        st.success(f"Logged in as **{st.session_state.current_name}**")
        api_key = st.text_input("Gemini API Key:", type="password", help="Enable AI features")
        if st.button("Log Out", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
            
    st.header(f"Physician Portal: {st.session_state.current_name}")
    
    tab1, tab2 = st.tabs(["📅 Published Schedule", "🛑 Manage Time Off"])
    
    with tab1:
        if st.session_state.db_state["saved_schedule"]:
            df = pd.DataFrame(st.session_state.db_state["saved_schedule"])
            mask = df.apply(lambda row: row.astype(str).str.contains(st.session_state.current_name).any(), axis=1)
            st.dataframe(df[mask].drop(columns=['Day_Index'], errors='ignore'), use_container_width=True, hide_index=True)
        else:
            st.info("No schedules have been published for the current period.")

    with tab2:
        st.markdown("#### Submit Unavailable Hours")
        with st.container(border=True):
            st.markdown("🤖 **AI Assistant: Submit via text**")
            phys_req = st.text_input("Example: 'I need Day 2 off from 8am to 5pm'")
            if st.button("Parse Time Off Request", type="secondary"):
                if not api_key: st.error("API Key required in sidebar.")
                else:
                    with st.spinner("Parsing request..."):
                        try:
                            parsed = parse_timeoff_request(phys_req, api_key)
                            new_requests = []
                            for item in parsed:
                                new_requests.append({"physician": st.session_state.current_name, "day": item.get("day"), "start": item.get("start"), "end": item.get("end")})
                            st.session_state.db_state["global_unavail"].extend(new_requests)
                            save_data(st.session_state.db_state)
                            st.toast("AI successfully logged your time off!", icon="✅")
                            st.rerun()
                        except Exception as e:
                            st.error(f"AI Error: {e}")

        st.markdown("##### Or Use Manual Grid")
        default_unavail = pd.DataFrame([{"Day": 1, "Start Time": datetime.time(8, 0), "End Time": datetime.time(17, 0)}])
        edited_unavail = st.data_editor(default_unavail, num_rows="dynamic", hide_index=True, use_container_width=True)
        
        if st.button("Submit Grid Request", type="primary"):
            new_requests = []
            for _, row in edited_unavail.iterrows():
                if pd.notna(row.get("Start Time")) and pd.notna(row.get("End Time")):
                    new_requests.append({"physician": st.session_state.current_name, "day": safe_int(row.get("Day"), 1), "start": row["Start Time"].strftime("%H:%M"), "end": row["End Time"].strftime("%H:%M")})
            st.session_state.db_state["global_unavail"].extend(new_requests)
            save_data(st.session_state.db_state)
            st.toast("Time off successfully submitted!", icon="✅") 

# --- 6. ADMIN PORTAL ---
def admin_view():
    with st.sidebar:
        st.success(f"Admin: **{st.session_state.current_name}**")
        if st.button("Log Out", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
        st.divider()
        
        st.markdown("### ⚙️ Global Parameters")
        api_key = st.text_input("Gemini API Key:", type="password", help="Required for natural language parsing.")
        num_days = st.slider("Schedule Length (Days)", 7, 92, 90)
        start_day = st.selectbox("Quarter Starts On:", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
        min_rest_hours = st.number_input("Mandatory Rest (Hrs)", 0, 48, 12, help="Minimum gap required between shifts.")
        solver_timeout = st.slider("Max Solver Search Time (Seconds)", 10, 300, 60, help="Give the AI more time to solve complex puzzles.")

    st.header("Admin Command Center")
    tab_config, tab_engine, tab_master = st.tabs(["👥 1. Staff & Shift Configuration", "🧠 2. AI Scheduling Engine", "📊 3. Master Schedule & Export"])
    
    with tab_config:
        with st.container(border=True):
            st.markdown("🤖 **AI Assistant: Configure Database via Text**")
            col_txt, col_btn = st.columns([4, 1])
            with col_txt:
                ai_config_cmd = st.text_input("Instruction:", placeholder="E.g., 'Add a Swing Shift from 3pm to 11pm needing 2 headcount'")
            with col_btn:
                st.write("") 
                if st.button("Update Database", use_container_width=True):
                    if not api_key: st.error("API Key required in sidebar.")
                    else:
                        with st.spinner("Updating database..."):
                            try:
                                parsed = parse_config_updates(ai_config_cmd, api_key)
                                new_shifts, new_docs = [], []
                                current_shift_count = len(st.session_state.shifts_df)
                                current_doc_count = len(st.session_state.physicians_df)
                                
                                for item in parsed.get("updates", []):
                                    if item.get("type") == "add_shift":
                                        current_shift_count += 1
                                        new_task_id = f"TSK-{current_shift_count:02d}"
                                        new_shifts.append({"Task ID": new_task_id, "Shift Name": item.get("Shift Name", "New Shift"), "Start Time": datetime.datetime.strptime(item.get("Start Time", "00:00"), "%H:%M").time(), "End Time": datetime.datetime.strptime(item.get("End Time", "00:00"), "%H:%M").time(), "Req Headcount": item.get("Req Headcount", 1)})
                                    elif item.get("type") == "add_physician":
                                        current_doc_count += 1
                                        new_doc_id = f"DOC-{current_doc_count:02d}"
                                        new_docs.append({"Provider ID": new_doc_id, "Name": item.get("Name", "New Doc"), "Max Total": item.get("Max Total", 0), "Max Nights": item.get("Max Nights", 0), "Max Weekends": item.get("Max Weekends", 0)})
                                
                                if new_shifts: st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, pd.DataFrame(new_shifts)], ignore_index=True)
                                if new_docs: st.session_state.physicians_df = pd.concat([st.session_state.physicians_df, pd.DataFrame(new_docs)], ignore_index=True)
                                st.rerun()
                            except Exception as e: st.error(f"Failed. Error: {e}")

        col_shifts, col_docs = st.columns([1, 1.5])
        with col_shifts:
            st.markdown("#### Shift Definitions")
            edited_shifts = st.data_editor(st.session_state.shifts_df, num_rows="dynamic", hide_index=True, use_container_width=True)
            st.session_state.shifts_df = edited_shifts 
            
        with col_docs:
            st.markdown("#### Provider Contracts")
            edited_physicians = st.data_editor(st.session_state.physicians_df, num_rows="dynamic", hide_index=True, use_container_width=True)
            st.session_state.physicians_df = edited_physicians 

        st.divider()
        physicians_list = [r["Name"] for _, r in edited_physicians.iterrows() if pd.notna(r.get("Name")) and str(r.get("Name")).strip()]
        carryover_docs = st.multiselect("Boundary Management (Quarter Carryover)", physicians_list)

    # Bulletproof Dictionary Comprehensions
    shift_reqs = {str(r["Shift Name"]).strip(): safe_int(r.get("Req Headcount")) for _, r in edited_shifts.iterrows() if pd.notna(r.get("Shift Name")) and str(r.get("Shift Name")).strip()}
    shift_times = {str(r["Shift Name"]).strip(): {"start": r["Start Time"], "end": r["End Time"]} for _, r in edited_shifts.iterrows() if pd.notna(r.get("Shift Name")) and str(r.get("Shift Name")).strip()}
    shift_ids = {str(r["Shift Name"]).strip(): r.get("Task ID") for _, r in edited_shifts.iterrows() if pd.notna(r.get("Shift Name")) and str(r.get("Shift Name")).strip()}
    shifts_list = list(shift_reqs.keys())

    p_limits = {str(r["Name"]).strip(): safe_int(r.get("Max Total")) for _, r in edited_physicians.iterrows() if pd.notna(r.get("Name")) and str(r.get("Name")).strip()}
    n_limits = {str(r["Name"]).strip(): safe_int(r.get("Max Nights")) for _, r in edited_physicians.iterrows() if pd.notna(r.get("Name")) and str(r.get("Name")).strip()}
    w_limits = {str(r["Name"]).strip(): safe_int(r.get("Max Weekends")) for _, r in edited_physicians.iterrows() if pd.notna(r.get("Name")) and str(r.get("Name")).strip()}
    p_ids = {str(r["Name"]).strip(): r.get("Provider ID") for _, r in edited_physicians.iterrows() if pd.notna(r.get("Name")) and str(r.get("Name")).strip()}

    day_offset = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}[start_day]
    weekend_days = [d for d in range(num_days) if (d + day_offset) % 7 in [5, 6]]
    night_idx = [i for i, s in enumerate(shifts_list) if "night" in s.lower()]

    with tab_engine:
        col_ai, col_requests = st.columns([2, 1])
        with col_requests:
            req_count = len(st.session_state.db_state["global_unavail"])
            st.metric("Pending Time-Off Requests", req_count)
            with st.expander("Review Provider Submissions"):
                if req_count > 0:
                    st.dataframe(st.session_state.db_state["global_unavail"], use_container_width=True)
                    if st.button("Clear All Requests", use_container_width=True):
                        st.session_state.db_state["global_unavail"] = []
                        save_data(st.session_state.db_state)
                        st.rerun()
                else:
                    st.write("No pending requests.")

        with col_ai:
            st.markdown("#### AI Natural Language Constraints")
            user_req = st.text_area("Input custom overrides or soft preferences:", "Dr. Patel prefers Day Shifts.", height=150)
            
            if st.button("🚀 Generate Optimal Schedule", type="primary", use_container_width=True):
                if not api_key: st.error("API Key required in sidebar."); st.stop()
                
                total_shifts_needed = sum(shift_reqs.values()) * num_days
                total_capacity = sum(p_limits.values())
                
                if total_shifts_needed > total_capacity:
                    st.error(f"🚨 **CAPACITY ERROR:** You need {total_shifts_needed} total shifts covered, but your doctors can only work a combined max of {total_capacity}. The Ghost Doctor will cover the remainder.")
                    st.toast("Warning: Unassigned Gaps will be generated.", icon="⚠️")
                    
                if night_idx:
                    total_nights_needed = sum(shift_reqs[shifts_list[i]] for i in night_idx) * num_days
                    total_night_capacity = sum(n_limits.values())
                    if total_nights_needed > total_night_capacity:
                        st.error(f"🚨 **NIGHT SHIFT ERROR:** You need {total_nights_needed} night shifts covered, but your staff limits only allow {total_night_capacity}.")
                        
                if weekend_days:
                    total_weekends_needed = sum(shift_reqs.values()) * len(weekend_days)
                    total_weekend_capacity = sum(w_limits.values())
                    if total_weekends_needed > total_weekend_capacity:
                         st.error(f"🚨 **WEEKEND ERROR:** You need {total_weekends_needed} weekend shifts covered, but your staff limits only allow {total_weekend_capacity}.")

                rules = []
                with st.status("Initializing Engine...", expanded=True) as status:
                    st.write("Translating Natural Language to Math...")
                    try: rules = parse_constraints(user_req, api_key, physicians_list, shifts_list)
                    except Exception as e: 
                        status.update(label="AI Parsing Failed", state="error")
                        st.error(f"AI Error: {e}"); st.stop()

                    st.write("Processing Provider Time-Off Logs...")
                    for req in st.session_state.db_state["global_unavail"]:
                        req_start = datetime.datetime.strptime(req['start'], "%H:%M").time()
                        req_end = datetime.datetime.strptime(req['end'], "%H:%M").time()
                        for s_name, s_info in shift_times.items():
                            if times_overlap(s_info['start'], s_info['end'], req_start, req_end):
                                rules.append({"physician_name": req['physician'], "constraint_type": "hard_time_off", "target_day": req['day'], "target_shift": s_name})

                    st.write(f"Running OR-Tools Mathematical Optimization (Timeout: {solver_timeout}s)...")
                    internal_
