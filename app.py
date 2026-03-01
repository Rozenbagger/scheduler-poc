import streamlit as st
import pandas as pd
from google import genai
from google.genai import types
import json
import datetime
import os
from ortools.sat.python import cp_model

# --- 1. THEME & SETTINGS ---
st.set_page_config(page_title="Shift Command Pro", page_icon="🏥", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stButton>button { border-radius: 8px; height: 3em; width: 100%; transition: 0.3s; background-color: #007bff; color: white; }
    .stButton>button:hover { background-color: #0056b3; border-color: #0056b3; }
    .stDataFrame { border-radius: 12px; overflow: hidden; }
    [data-testid="stMetricValue"] { font-size: 24px; color: #007bff; }
    </style>
    """, unsafe_allow_stdio=True)

DB_FILE = "local_database.json"

def load_data():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            data = json.load(f)
            for req in data.get("global_unavail", []):
                if "date" not in req:
                    req["date"] = datetime.date.today().strftime("%Y-%m-%d")
            return data
    return {"global_unavail": [], "saved_schedule": None}

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

if "db_state" not in st.session_state:
    st.session_state.db_state = load_data()

# Initialize dynamic DataFrames for AI modifications
if "shifts_df" not in st.session_state:
    st.session_state.shifts_df = pd.DataFrame([
        {"Task ID": "TSK-01", "Shift Name": "Day Shift", "Start Time": datetime.time(7, 0), "End Time": datetime.time(15, 0), "Req Headcount": 2},
        {"Task ID": "TSK-02", "Shift Name": "Night Shift", "Start Time": datetime.time(23, 0), "End Time": datetime.time(7, 0), "Req Headcount": 1},
        {"Task ID": "TSK-03", "Shift Name": "24h Sick Call", "Start Time": datetime.time(7, 0), "End Time": datetime.time(7, 0), "Req Headcount": 1}
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

# --- 3. HELPER FUNCTIONS & NEW GENAI SDK PARSERS ---
def parse_constraints(user_text, key, roster, shifts):
    client = genai.Client(api_key=key)
    prompt = f"""
    You are a scheduling assistant. Extract scheduling rules. Roster: {roster}. Shifts: {shifts}.
    Respond ONLY with a JSON array: [{{ "physician_name": "exact name", "constraint_type": "soft_prefer_shift" OR "soft_avoid_shift" OR "hard_time_off", "target_date": "YYYY-MM-DD" or null, "target_shift": "exact shift name" or null }}]
    Text: "{user_text}"
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        )
    )
    return json.loads(response.text)

def parse_config_modifications(user_text, key, current_docs, current_shifts):
    client = genai.Client(api_key=key)
    prompt = f"""
    You are a hospital database administrator. Convert the user's request into structured Upsert commands.
    Current Physicians JSON: {current_docs}
    Current Shifts JSON: {current_shifts}
    
    RULES:
    1. To UPDATE an existing item, include its exact existing 'Provider ID' or 'Task ID' and ONLY the fields that need changing.
    2. To ADD a new item, omit the ID field (or leave it blank). The system will generate one automatically. Ensure all other required fields are present.
    3. Ensure times are formatted strictly as "HH:MM".
    
    Respond ONLY with a JSON matching this schema:
    {{
      "upsert_physicians": [{{ "Provider ID": "optional existing ID", "Name": "...", "Max Total": int, "Max Nights": int, "Max Weekends": int }}],
      "upsert_shifts": [{{ "Task ID": "optional existing ID", "Shift Name": "...", "Start Time": "HH:MM", "End Time": "HH:MM", "Req Headcount": int }}]
    }}
    Text: "{user_text}"
    """
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        )
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
        st.write(""); st.write("")
        st.image("https://cdn-icons-png.flaticon.com/512/2966/2966327.png", width=80) 
        st.title("Shift Command")
        st.markdown("### Enterprise Medical Scheduling\nPlease authenticate to access your portal.")
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
                    st.error("Authentication failed. Check your credentials.")

# --- 5. PHYSICIAN PORTAL ---
def physician_view():
    with st.sidebar:
        st.success(f"Logged in as **{st.session_state.current_name}**")
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
            st.info("No schedules have been published.")

    with tab2:
        st.markdown("#### Submit Unavailable Hours")
        default_unavail = pd.DataFrame([{"Date": datetime.date.today(), "Start Time": datetime.time(8, 0), "End Time": datetime.time(17, 0)}])
        edited_unavail = st.data_editor(default_unavail, num_rows="dynamic", hide_index=True, use_container_width=True)
        if st.button("Submit Time Off Request", type="primary"):
            new_requests = []
            for _, row in edited_unavail.iterrows():
                if not pd.isna(row.get("Start Time")):
                    new_requests.append({
                        "physician": st.session_state.current_name,
                        "date": row["Date"].strftime("%Y-%m-%d"),
                        "start": row["Start Time"].strftime("%H:%M"),
                        "end": row["End Time"].strftime("%H:%M")
                    })
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
        
        schedule_start_date = st.date_input("Schedule Start Date", datetime.date.today())
        schedule_end_date = st.date_input("Schedule End Date", datetime.date.today() + datetime.timedelta(days=29))
        
        if schedule_end_date < schedule_start_date:
            st.error("End date must be after start date.")
            st.stop()
            
        num_days = (schedule_end_date - schedule_start_date).days + 1
        day_offset = schedule_start_date.weekday() 
        min_rest_hours = st.number_input("Mandatory Rest (Hrs)", 0, 48, 12)

    st.header("Admin Command Center")
    tab_config, tab_engine, tab_master = st.tabs(["👥 1. Staff & Shifts", "🧠 2. AI Engine", "📊 3. Master & Export"])
    
    with tab_config:
        st.markdown("#### ✨ AI Configuration Assistant")
        col_config, col_btn = st.columns([4, 1])
        with col_config:
            config_prompt = st.text_input("Ask AI to add or edit physicians/shifts:", placeholder="e.g., 'Update Dr. Smith to max 40 shifts. Add a 12h Swing shift.'", label_visibility="collapsed")
        with col_btn:
            if st.button("Modify Models", use_container_width=True):
                if not api_key: 
                    st.error("API Key required.")
                else:
                    with st.spinner("AI is executing database operations..."):
                        c_docs = st.session_state.physicians_df.to_json(orient="records", date_format="iso")
                        c_shifts = st.session_state.shifts_df.to_json(orient="records", date_format="iso")
                        
                        updates = parse_config_modifications(config_prompt, api_key, c_docs, c_shifts)
                        
                        if "upsert_physicians" in updates:
                            for p in updates["upsert_physicians"]:
                                pid = p.get("Provider ID", "")
                                if pid in st.session_state.physicians_df["Provider ID"].values:
                                    for col, val in p.items():
                                        if col in st.session_state.physicians_df.columns and col != "Provider ID":
                                            st.session_state.physicians_df.loc[st.session_state.physicians_df["Provider ID"] == pid, col] = val
                                else:
                                    existing_nums = [int(x.split('-')[1]) for x in st.session_state.physicians_df["Provider ID"] if '-' in str(x) and str(x).split('-')[1].isdigit()]
                                    p["Provider ID"] = f"DOC-{(max(existing_nums) + 1 if existing_nums else 1):02d}"
                                    st.session_state.physicians_df = pd.concat([st.session_state.physicians_df, pd.DataFrame([p])], ignore_index=True)
                        
                        if "upsert_shifts" in updates:
                            for s in updates["upsert_shifts"]:
                                tid = s.get("Task ID", "")
                                if "Start Time" in s: s["Start Time"] = pd.to_datetime(s["Start Time"]).time()
                                if "End Time" in s: s["End Time"] = pd.to_datetime(s["End Time"]).time()
                                
                                if tid in st.session_state.shifts_df["Task ID"].values:
                                    for col, val in s.items():
                                        if col in st.session_state.shifts_df.columns and col != "Task ID":
                                            st.session_state.shifts_df.loc[st.session_state.shifts_df["Task ID"] == tid, col] = val
                                else:
                                    existing_nums = [int(x.split('-')[1]) for x in st.session_state.shifts_df["Task ID"] if '-' in str(x) and str(x).split('-')[1].isdigit()]
                                    s["Task ID"] = f"TSK-{(max(existing_nums) + 1 if existing_nums else 1):02d}"
                                    st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, pd.DataFrame([s])], ignore_index=True)
                        
                        st.rerun()
        st.divider()

        col_shifts, col_docs = st.columns([1, 1.5])
        
        with col_shifts:
            st.markdown("#### Shift Definitions")
            edited_shifts = st.data_editor(st.session_state.shifts_df, num_rows="dynamic", hide_index=True, use_container_width=True, disabled=["Task ID"])
            
            needs_update = False
            for idx, row in edited_shifts.iterrows():
                if pd.isna(row["Task ID"]) or str(row["Task ID"]).strip() == "":
                    existing = [int(x.split('-')[1]) for x in edited_shifts["Task ID"].dropna() if '-' in str(x) and str(x).split('-')[1].isdigit()]
                    edited_shifts.at[idx, "Task ID"] = f"TSK-{(max(existing) + 1 if existing else 1):02d}"
                    needs_update = True
            st.session_state.shifts_df = edited_shifts
            if needs_update: st.rerun()

        with col_docs:
            st.markdown("#### Provider Contracts")
            edited_docs = st.data_editor(st.session_state.physicians_df, num_rows="dynamic", hide_index=True, use_container_width=True, disabled=["Provider ID"])
            
            needs_update_docs = False
            for idx, row in edited_docs.iterrows():
                if pd.isna(row["Provider ID"]) or str(row["Provider ID"]).strip() == "":
                    existing = [int(x.split('-')[1]) for x in edited_docs["Provider ID"].dropna() if '-' in str(x) and str(x).split('-')[1].isdigit()]
                    edited_docs.at[idx, "Provider ID"] = f"DOC-{(max(existing) + 1 if existing else 1):02d}"
                    needs_update_docs = True
            st.session_state.physicians_df = edited_docs
            if needs_update_docs: st.rerun()

        st.divider()
        physicians_list = [r["Name"] for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")]
        carryover_docs = st.multiselect("Select providers who worked the final overnight shift of the previous period:", physicians_list)

    # Process Data logic
    shift_reqs = {r["Shift Name"]: int(r["Req Headcount"]) for _, r in st.session_state.shifts_df.iterrows() if r.get("Shift Name")}
    shift_times = {r["Shift Name"]: {"start": r["Start Time"], "end": r["End Time"]} for _, r in st.session_state.shifts_df.iterrows() if r.get("Shift Name")}
    shift_ids = {r["Shift Name"]: r["Task ID"] for _, r in st.session_state.shifts_df.iterrows() if r.get("Shift Name")}
    shifts_list = list(shift_reqs.keys())

    p_limits = {r["Name"]: int(r["Max Total"]) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    n_limits = {r["Name"]: int(r["Max Nights"]) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    w_limits = {r["Name"]: int(r["Max Weekends"]) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    p_ids = {r["Name"]: r["Provider ID"] for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}

    weekend_days = [d for d in range(num_days) if (d + day_offset) % 7 in [5, 6]]
    night_idx = [i for i, s in enumerate(shifts_list) if any(x in s.lower() for x in ["night", "24h", "sick"])]

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
                        st.toast("Requests cleared.", icon="🗑️")
                        st.rerun()
                else:
                    st.write("No pending requests.")

        with col_ai:
            st.markdown("#### AI Natural Language Rules")
            user_req = st.text_area("Input custom overrides or soft preferences:", f"Dr. Patel prefers Day Shifts. Dr. Jones needs {schedule_start_date.strftime('%Y-%m-%d')} off.", height=150)
            
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
                        req_date = datetime.datetime.strptime(req['date'], "%Y-%m-%d").date()
                        target_d = (req_date - schedule_start_date).days
                        
                        if 0 <= target_d < num_days:
                            req_start = datetime.datetime.strptime(req['start'], "%H:%M").time()
                            req_end = datetime.datetime.strptime(req['end'], "%H:%M").time()
                            for s_name, s_info in shift_times.items():
                                if times_overlap(s_info['start'], s_info['end'], req_start, req_end):
                                    rules.append({"physician_name": req['physician'], "constraint_type": "hard_time_off", "target_d": target_d, "target_shift": s_name})

                    st.write(f"Running OR-Tools Mathematical Optimization for {num_days} days...")
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
                        c_type = r.get("constraint_type")
                        t_s = shifts_list.index(r["target_shift"]) if r.get("target_shift") in shifts_list else None
                        
                        t_d = None
                        if "target_d" in r:
                            t_d = r["target_d"]
                        elif r.get("target_date"):
                            try:
                                t_date = datetime.datetime.strptime(r.get("target_date"), "%Y-%m-%d").date()
                                t_d = (t_date - schedule_start_date).days
                            except ValueError: pass
                        
                        if t_d is not None and not (0 <= t_d < num_days): continue
                        
                        if c_type == "hard_time_off":
                            for d in ([t_d] if t_d is not None else range(num_days)):
                                for s in ([t_s] if t_s is not None else range(len(shifts_list))):
                                    if 0 <= d < num_days: model.Add(shifts[(p, d, s)] == 0)
                        elif c_type == "soft_prefer_shift" and t_s is not None:
                            for d in ([t_d] if t_d is not None else range(num_days)):
                                if 0 <= d < num_days: obj_terms.append(shifts[(p, d, t_s)] * 10)
                        elif c_type == "soft_avoid_shift" and t_s is not None:
                            for d in ([t_d] if t_d is not None else range(num_days)):
                                if 0 <= d < num_days: obj_terms.append(shifts[(p, d, t_s)] * -10)

                    for d in range(num_days):
                        for s in range(len(shifts_list)): obj_terms.append(shifts[(ghost_idx, d, s)] * -10000)

                    if obj_terms: model.Maximize(sum(obj_terms))

                    solver = cp_model.CpSolver()
                    solver.parameters.max_time_in_seconds = 30.0 

                    if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                        grid = []
                        for d in range(num_days):
                            current_date = schedule_start_date + datetime.timedelta(days=d)
                            date_str = current_date.strftime("%Y-%m-%d")
                            day_name = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][current_date.weekday()]
                            
                            row = {"Date": f"{date_str} ({day_name})", "Day_Index": d}
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
            flat = []
            for idx, r in df.iterrows():
                date_str = (schedule_start_date + datetime.timedelta(days=r["Day_Index"])).strftime("%Y-%m-%d")
                for s_name in shifts_list:
                    for doc in [d.strip() for d in str(r[s_name]).split(",") if d.strip() and d.strip() != "nan"]:
                        flat.append({
                            "Date": date_str,
                            "Provider_ID": p_ids.get(doc, "SYS-GAP"),
                            "Task_ID": shift_ids.get(s_name, "UNKNOWN"),
                            "Start": shift_times[s_name]['start'].strftime("%H:%M"),
                            "End": shift_times[s_name]['end'].strftime("%H:%M")
                        })
            
            st.download_button("📥 Download QGenda / Amion CSV Payload", pd.DataFrame(flat).to_csv(index=False).encode('utf-8'), 'enterprise_export.csv', 'text/csv', type="secondary")
        else:
            st.info("No schedule has been generated yet. Navigate to the AI Engine tab to build one.")

# --- 7. ROUTER ---
if not st.session_state.logged_in: login_screen()
elif st.session_state.current_role == "Admin": admin_view()
else: physician_view()
