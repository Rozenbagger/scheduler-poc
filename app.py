import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import datetime
import os
from ortools.sat.python import cp_model

# --- 1. SETTINGS & LOCAL DB ---
st.set_page_config(page_title="Medical Scheduler Pro", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

DB_FILE = "local_database.json"

def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {"global_unavail": [], "saved_schedule": None}

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

if "db_state" not in st.session_state:
    st.session_state.db_state = load_data()

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
def parse_constraints(user_text, key, roster, shifts):
    genai.configure(api_key=key)
    model = genai.GenerativeModel('gemini-1.5-flash', generation_config={"response_mime_type": "application/json"})
    prompt = f"""
    You are a scheduling assistant. Extract scheduling rules. Roster: {roster}. Shifts: {shifts}.
    Respond ONLY with a JSON array: [{{ "physician_name": "exact name", "constraint_type": "soft_prefer_shift" OR "soft_avoid_shift" OR "hard_time_off", "target_day": integer or null, "target_shift": "exact shift name" or null }}]
    Text: "{user_text}"
    """
    return json.loads(model.generate_content(prompt).text)

def times_overlap(s_start, s_end, u_start, u_end):
    if pd.isna(s_start) or pd.isna(s_end) or pd.isna(u_start) or pd.isna(u_end): return False
    def to_mins(t): return t.hour * 60 + t.minute
    ss, se, us, ue = to_mins(s_start), to_mins(s_end), to_mins(u_start), to_mins(u_end)
    if se <= ss: se += 24 * 60
    if ue <= us: ue += 24 * 60
    return max(ss, us) < min(se, ue)

# --- 4. LOGIN SCREEN (Redesigned) ---
def login_screen():
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.write("") # Spacing
        st.write("")
        st.image("https://cdn-icons-png.flaticon.com/512/2966/2966327.png", width=80) # Generic medical icon
        st.title("Shift Command")
        st.markdown("### Enterprise Medical Scheduling")
        st.markdown("Please authenticate to access your portal.")
        
        with st.form("login_form", clear_on_submit=True):
            username = st.text_input("Username").lower()
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Authenticate", use_container_width=True)
            
            if submitted:
                if username in USER_DB and USER_DB[username]["password"] == password:
                    st.session_state.logged_in = True
                    st.session_state.current_role = USER_DB[username]["role"]
                    st.session_state.current_name = USER_DB[username]["name"]
                    st.rerun()
                else:
                    st.error("Authentication failed. Please check your credentials.")

# --- 5. PHYSICIAN PORTAL (Redesigned) ---
def physician_view():
    with st.sidebar:
        st.success(f"Logged in as **{st.session_state.current_name}**")
        if st.button("Log Out", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
            
    st.header(f"Physician Portal: {st.session_state.current_name}")
    st.markdown("View your upcoming assignments or manage your availability.")
    
    # UX Enhancement: Tabbed Interface
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
        st.markdown("Enter blocks of time you are unable to work. Management will route around these.")
        default_unavail = pd.DataFrame([{"Day": 1, "Start Time": datetime.time(8, 0), "End Time": datetime.time(17, 0)}])
        edited_unavail = st.data_editor(default_unavail, num_rows="dynamic", hide_index=True, use_container_width=True)
        
        if st.button("Submit Time Off Request", type="primary"):
            new_requests = []
            for _, row in edited_unavail.iterrows():
                if not pd.isna(row.get("Start Time")):
                    new_requests.append({
                        "physician": st.session_state.current_name,
                        "day": int(row["Day"]),
                        "start": row["Start Time"].strftime("%H:%M"),
                        "end": row["End Time"].strftime("%H:%M")
                    })
            st.session_state.db_state["global_unavail"].extend(new_requests)
            save_data(st.session_state.db_state)
            st.toast("Time off successfully submitted!", icon="✅") # UX Enhancement: Toast instead of green box

# --- 6. ADMIN PORTAL (Redesigned) ---
def admin_view():
    # UX Enhancement: Clean Sidebar reserved for global variables
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
        min_rest_hours = st.number_input("Mandatory Rest (Hrs)", 0, 48, 12, help="Minimum gap required between shifts for a single physician.")

    st.header("Admin Command Center")
    
    # UX Enhancement: Logical workflow via Tabs
    tab_config, tab_engine, tab_master = st.tabs(["👥 1. Staff & Shift Configuration", "🧠 2. AI Scheduling Engine", "📊 3. Master Schedule & Export"])
    
    with tab_config:
        st.markdown("Configure your hospital's capacity and shift requirements. These tables define the absolute mathematical boundaries.")
        
        col_shifts, col_docs = st.columns([1, 1.5])
        with col_shifts:
            st.markdown("#### Shift Definitions")
            default_shifts = pd.DataFrame([
                {"Task ID": "TSK-01", "Shift Name": "Day Shift", "Start Time": datetime.time(7, 0), "End Time": datetime.time(15, 0), "Req Headcount": 2},
                {"Task ID": "TSK-02", "Shift Name": "Night Shift", "Start Time": datetime.time(23, 0), "End Time": datetime.time(7, 0), "Req Headcount": 1}
            ])
            edited_shifts = st.data_editor(default_shifts, num_rows="dynamic", hide_index=True, use_container_width=True)
            
        with col_docs:
            st.markdown("#### Provider Contracts")
            default_physicians = pd.DataFrame([
                {"Provider ID": "DOC-01", "Name": "Dr. Smith", "Max Total": 30, "Max Nights": 10, "Max Weekends": 10},
                {"Provider ID": "DOC-02", "Name": "Dr. Jones", "Max Total": 30, "Max Nights": 10, "Max Weekends": 10},
                {"Provider ID": "DOC-03", "Name": "Dr. Patel", "Max Total": 45, "Max Nights": 15, "Max Weekends": 15}
            ])
            edited_physicians = st.data_editor(default_physicians, num_rows="dynamic", hide_index=True, use_container_width=True)

        st.divider()
        st.markdown("#### Boundary Management (Quarter Carryover)")
        physicians_list = [r["Name"] for _, r in edited_physicians.iterrows() if r.get("Name")]
        carryover_docs = st.multiselect("Select providers who worked the final overnight shift of the previous quarter:", physicians_list, help="These providers will be mathematically locked out of Day 1 morning shifts.")

    # Process Data logic (Hidden from UI)
    shift_reqs = {r["Shift Name"]: int(r["Req Headcount"]) for _, r in edited_shifts.iterrows() if r.get("Shift Name")}
    shift_times = {r["Shift Name"]: {"start": r["Start Time"], "end": r["End Time"]} for _, r in edited_shifts.iterrows() if r.get("Shift Name")}
    shift_ids = {r["Shift Name"]: r["Task ID"] for _, r in edited_shifts.iterrows() if r.get("Shift Name")}
    shifts_list = list(shift_reqs.keys())

    p_limits = {r["Name"]: int(r["Max Total"]) for _, r in edited_physicians.iterrows() if r.get("Name")}
    n_limits = {r["Name"]: int(r["Max Nights"]) for _, r in edited_physicians.iterrows() if r.get("Name")}
    w_limits = {r["Name"]: int(r["Max Weekends"]) for _, r in edited_physicians.iterrows() if r.get("Name")}
    p_ids = {r["Name"]: r["Provider ID"] for _, r in edited_physicians.iterrows() if r.get("Name")}

    day_offset = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}[start_day]
    weekend_days = [d for d in range(num_days) if (d + day_offset) % 7 in [5, 6]]
    night_idx = [i for i, s in enumerate(shifts_list) if "night" in s.lower()]

    with tab_engine:
        col_ai, col_requests = st.columns([2, 1])
        
        with col_requests:
            # UX Enhancement: Status Metric
            req_count = len(st.session_state.db_state["global_unavail"])
            st.metric("Pending Time-Off Requests", req_count)
            
            with st.expander("Review Provider Submissions"):
                if req_count > 0:
                    st.dataframe(st.session_state.db_state["global_unavail"], use_container_width=True)
                    if st.button("Clear All Requests", use_container_width=True):
                        st.session_state.db_state["global_unavail"] = []
                        save_data(st.session_state.db_state)
                        st.toast("Requests cleared.", icon="🗑️")
                        st.rerun()
                else:
                    st.write("No pending requests.")

        with col_ai:
            st.markdown("#### AI Natural Language Rules")
            user_req = st.text_area("Input custom overrides, sub-specialty requirements, or soft preferences:", "Dr. Patel prefers Day Shifts.", height=150)
            
            if st.button("🚀 Generate Optimal Schedule", type="primary", use_container_width=True):
                if not api_key: 
                    st.error("API Key required in sidebar.")
                    st.stop()
                
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

                    st.write("Running OR-Tools Mathematical Optimization (Timeout: 30s)...")
                    internal_physicians = physicians_list + ["⚠️ UNASSIGNED GAP"]
                    i_limits, i_n_limits, i_w_limits = p_limits.copy(), n_limits.copy(), w_limits.copy()
                    i_limits["⚠️ UNASSIGNED GAP"] = i_n_limits["⚠️ UNASSIGNED GAP"] = i_w_limits["⚠️ UNASSIGNED GAP"] = 999 
                    ghost_idx = len(internal_physicians) - 1

                    model = cp_model.CpModel()
                    shifts = {(p, d, s): model.NewBoolVar(f's_{p}_{d}_{s}') for p in range(len(internal_physicians)) for d in range(num_days) for s in range(len(shifts_list))}
                    obj_terms = [] 
                    
                    for d in range(num_days):
                        for s, s_name in enumerate(shifts_list):
                            model.Add(sum(shifts[(p, d, s)] for p in range(len(internal_physicians))) == shift_reqs[s_name])

                    for p in range(len(internal_physicians)):
                        for d in range(num_days): model.AddAtMostOne(shifts[(p, d, s)] for s in range(len(shifts_list)))
                        model.Add(sum(shifts[(p, d, s)] for d in range(num_days) for s in range(len(shifts_list))) <= i_limits[internal_physicians[p]])
                        if night_idx: model.Add(sum(shifts[(p, d, s)] for d in range(num_days) for s in night_idx) <= i_n_limits[internal_physicians[p]])
                        if weekend_days: model.Add(sum(shifts[(p, d, s)] for d in weekend_days for s in range(len(shifts_list))) <= i_w_limits[internal_physicians[p]])

                    for doc_name in carryover_docs:
                        if doc_name in internal_physicians:
                            p = internal_physicians.index(doc_name)
                            for s, s_name in enumerate(shifts_list):
                                t_start = shift_times[s_name]['start']
                                if (t_start.hour * 60 + t_start.minute) < (min_rest_hours * 60):
                                    model.Add(shifts[(p, 0, s)] == 0)

                    shift_ints = {}
                    for d in range(num_days):
                        for s, s_name in enumerate(shifts_list):
                            t = shift_times[s_name]
                            start_m, end_m = d*1440 + t['start'].hour*60 + t['start'].minute, d*1440 + t['end'].hour*60 + t['end'].minute
                            if end_m <= start_m: end_m += 1440
                            shift_ints[(d, s)] = (start_m, end_m)

                    keys = list(shift_ints.keys())
                    for p, p_name in enumerate(internal_physicians):
                        if p_name == "⚠️ UNASSIGNED GAP": continue 
                        for i in range(len(keys)):
                            for j in range(i + 1, len(keys)):
                                d1, s1 = keys[i]; d2, s2 = keys[j]
                                st1, en1 = shift_ints[(d1, s1)]; st2, en2 = shift_ints[(d2, s2)]
                                gap = st2 - en1 if st2 >= en1 else (st1 - en2 if st1 >= en2 else -1)
                                if gap < min_rest_hours * 60:
                                    model.Add(shifts[(p, d1, s1)] + shifts[(p, d2, s2)] <= 1)

                    for r in rules:
                        if r.get("physician_name") not in physicians_list: continue 
                        p = internal_physicians.index(r["physician_name"])
                        c_type, t_d, t_s = r.get("constraint_type"), (r["target_day"] - 1) if r.get("target_day") else None, shifts_list.index(r["target_shift"]) if r.get("target_shift") in shifts_list else None
                        
                        if c_type == "hard_time_off":
                            for d in ([t_d] if t_d is not None else range(num_days)):
                                for s in ([t_s] if t_s is not None else range(len(shifts_list))):
                                    if 0 <= d < num_days: model.Add(shifts[(p, d, s)] == 0)
                        elif c_type == "soft_prefer_shift" and t_s is not None:
                            for d in ([t_d] if t_d is not None else range(num_days)):
                                if 0 <= d < num_days: obj_terms.append(shifts[(p, d, t_s)] * 10)

                    for d in range(num_days):
                        for s in range(len(shifts_list)): obj_terms.append(shifts[(ghost_idx, d, s)] * -10000)

                    if obj_terms: model.Maximize(sum(obj_terms))

                    solver = cp_model.CpSolver()
                    solver.parameters.max_time_in_seconds = 30.0 

                    if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                        grid = []
                        for d in range(num_days):
                            row = {"Day": f"Day {d+1} ({['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][(d + day_offset) % 7]})", "Day_Index": d}
                            for s, s_name in enumerate(shifts_list):
                                docs = [internal_physicians[p] for p in range(len(internal_physicians)) if solver.Value(shifts[(p, d, s)]) == 1]
                                row[s_name] = ", ".join(docs)
                            grid.append(row)
                        
                        st.session_state.db_state["saved_schedule"] = grid
                        save_data(st.session_state.db_state)
                        status.update(label="Mathematical Schedule Generated!", state="complete", expanded=False)
                        st.toast("New schedule published to Master view.", icon="🎉")
                    else: 
                        status.update(label="Infeasible Ruleset", state="error")
                        st.error("Solver failed. The combination of constraints and time off requested is mathematically impossible.")

    with tab_master:
        if st.session_state.db_state["saved_schedule"]:
            df = pd.DataFrame(st.session_state.db_state["saved_schedule"])
            display_df = df.drop(columns=['Day_Index'])
            st.dataframe(display_df.style.map(lambda v: 'background-color: #ffcccc; color: #990000; font-weight: bold' if '⚠️ UNASSIGNED GAP' in str(v) else ''), use_container_width=True, hide_index=True)

            st.markdown("#### Enterprise Integration")
            col_date, col_btn = st.columns([1, 2])
            with col_date:
                start_date = st.date_input("Map 'Day 1' to Real-World Date:")
            
            flat = []
            for idx, r in df.iterrows():
                date_str = (start_date + datetime.timedelta(days=r["Day_Index"])).strftime("%Y-%m-%d")
                for s_name in shifts_list:
                    for doc in [d.strip() for d in str(r[s_name]).split(",") if d.strip() and d.strip() != "nan"]:
                        flat.append({
                            "Date": date_str,
                            "Provider_ID": p_ids.get(doc, "SYS-GAP"),
                            "Task_ID": shift_ids.get(s_name, "UNKNOWN"),
                            "Start": shift_times[s_name]['start'].strftime("%H:%M"),
                            "End": shift_times[s_name]['end'].strftime("%H:%M")
                        })
            with col_btn:
                st.write("") # Alignment spacing
                st.download_button("📥 Download QGenda / Amion CSV Payload", pd.DataFrame(flat).to_csv(index=False).encode('utf-8'), 'enterprise_export.csv', 'text/csv', type="secondary")
        else:
            st.info("No schedule has been generated yet. Navigate to the AI Engine tab to build one.")

# --- 7. ROUTER ---
if not st.session_state.logged_in: login_screen()
elif st.session_state.current_role == "Admin": admin_view()
else: physician_view()
