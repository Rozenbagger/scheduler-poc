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
    """, unsafe_allow_html=True)

DB_FILE = "local_database.json"

def load_data():
    default_data = {
        "global_unavail": [],
        "global_preferences": [], # NEW: Storage for physician soft preferences
        "saved_schedule": None,
        "shifts": [
            {"Task ID": "TSK-01", "Shift Name": "Day Shift", "Zone": "Main ER", "Start Time": "07:00", "End Time": "15:00", "Req Headcount": 2},
            {"Task ID": "TSK-02", "Shift Name": "Night Shift", "Zone": "Main ER", "Start Time": "23:00", "End Time": "07:00", "Req Headcount": 1},
            {"Task ID": "TSK-03", "Shift Name": "24h Sick Call", "Zone": "On Call", "Start Time": "07:00", "End Time": "07:00", "Req Headcount": 1}
        ],
        "physicians": [
            {"Provider ID": "DOC-01", "Name": "Dr. Smith", "Min Total": 10, "Max Total": 30, "Min Nights": 2, "Max Nights": 10, "Min Weekends": 2, "Max Weekends": 10},
            {"Provider ID": "DOC-02", "Name": "Dr. Jones", "Min Total": 10, "Max Total": 30, "Min Nights": 2, "Max Nights": 10, "Min Weekends": 2, "Max Weekends": 10},
            {"Provider ID": "DOC-03", "Name": "Dr. Patel", "Min Total": 15, "Max Total": 45, "Min Nights": 4, "Max Nights": 15, "Min Weekends": 4, "Max Weekends": 15}
        ],
        "settings": {
            "api_key": "",
            "start_date": datetime.date.today().strftime("%Y-%m-%d"),
            "end_date": (datetime.date.today() + datetime.timedelta(days=29)).strftime("%Y-%m-%d"),
            "min_rest_hours": 12,
            "carryover_docs": []
        }
    }
    
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                for k, v in default_data.items():
                    if k not in data:
                        data[k] = v
                    elif isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            if sub_k not in data[k]:
                                data[k][sub_k] = sub_v
                for req in data.get("global_unavail", []):
                    if "date" not in req:
                        req["date"] = datetime.date.today().strftime("%Y-%m-%d")
                for doc in data.get("physicians", []):
                    if "Min Total" not in doc: doc["Min Total"] = 0
                    if "Min Nights" not in doc: doc["Min Nights"] = 0
                    if "Min Weekends" not in doc: doc["Min Weekends"] = 0
                return data
        except Exception:
            pass
    return default_data

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

if "db_state" not in st.session_state:
    st.session_state.db_state = load_data()

if "shifts_df" not in st.session_state:
    s_list = st.session_state.db_state["shifts"]
    for s in s_list:
        if isinstance(s.get("Start Time"), str):
            s["Start Time"] = datetime.datetime.strptime(s["Start Time"], "%H:%M").time()
        if isinstance(s.get("End Time"), str):
            s["End Time"] = datetime.datetime.strptime(s["End Time"], "%H:%M").time()
    st.session_state.shifts_df = pd.DataFrame(s_list)

if "physicians_df" not in st.session_state:
    st.session_state.physicians_df = pd.DataFrame(st.session_state.db_state["physicians"])

def save_current_state():
    s_df = st.session_state.shifts_df.copy()
    s_df["Start Time"] = s_df["Start Time"].apply(lambda x: x.strftime("%H:%M") if pd.notnull(x) else "00:00")
    s_df["End Time"] = s_df["End Time"].apply(lambda x: x.strftime("%H:%M") if pd.notnull(x) else "00:00")
    st.session_state.db_state["shifts"] = s_df.to_dict(orient="records")
    st.session_state.db_state["physicians"] = st.session_state.physicians_df.to_dict(orient="records")
    save_data(st.session_state.db_state)

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

# --- 3. HELPER FUNCTIONS & AI PARSERS ---
def parse_constraints(user_text, key, roster, shifts):
    client = genai.Client(api_key=key)
    prompt = f"""
    You are a scheduling assistant. Extract scheduling rules. Roster: {roster}. Shifts: {shifts}.
    Respond ONLY with a JSON array: [{{ "physician_name": "exact name", "constraint_type": "soft_prefer_shift" OR "soft_avoid_shift" OR "hard_time_off", "target_date": "YYYY-MM-DD" or null, "target_shift": "exact shift name" or null }}]
    Text: "{user_text}"
    """
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
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
      "upsert_physicians": [{{ "Provider ID": "optional existing ID", "Name": "...", "Min Total": int, "Max Total": int, "Min Nights": int, "Max Nights": int, "Min Weekends": int, "Max Weekends": int }}],
      "upsert_shifts": [{{ "Task ID": "optional existing ID", "Shift Name": "...", "Zone": "...", "Start Time": "HH:MM", "End Time": "HH:MM", "Req Headcount": int }}]
    }}
    Text: "{user_text}"
    """
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
    return json.loads(response.text)

# UPGRADED: Combined Time-Off and Preference Parser for Physicians
def parse_physician_ai_request(user_text, key, today_date, shifts_list):
    client = genai.Client(api_key=key)
    prompt = f"""
    You are a scheduling assistant for a physician. Extract time-off requests AND shift preferences from their text.
    Today's date is {today_date}. Available Shifts: {shifts_list}.
    If they request an entire day off, use start="00:00" and end="23:59". Ensure times are "HH:MM".
    
    Respond ONLY with a JSON object matching this schema:
    {{
      "time_off": [{{ "date": "YYYY-MM-DD", "start": "HH:MM", "end": "HH:MM" }}],
      "preferences": [{{ "constraint_type": "soft_prefer_shift" OR "soft_avoid_shift", "target_date": "YYYY-MM-DD" or null, "target_shift": "exact shift name" or null }}]
    }}
    Text: "{user_text}"
    """
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
    return json.loads(response.text)

def times_overlap(s_start, s_end, u_start, u_end):
    if pd.isna(s_start) or pd.isna(s_end) or pd.isna(u_start) or pd.isna(u_end): return False
    def to_mins(t): return t.hour * 60 + t.minute
    ss, se, us, ue = to_mins(s_start), to_mins(s_end), to_mins(u_start), to_mins(u_end)
    if se <= ss: se += 24 * 60
    if ue <= us: ue += 24 * 60
    return max(ss, us) < min(se, ue)

# --- VISUAL CALENDAR RENDERER ---
def render_calendar_view(saved_schedule):
    if not saved_schedule:
        st.info("No schedule has been generated yet.")
        return

    try:
        first_date_str = saved_schedule[0]["Date"]
        first_date = datetime.datetime.strptime(first_date_str, "%Y-%m-%d").date()
        start_weekday = first_date.weekday() 

        html = "<div style='display: grid; grid-template-columns: repeat(7, 1fr); gap: 10px; margin-top: 15px;'>"
        
        days_of_week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for d in days_of_week:
            html += f"<div style='text-align: center; font-weight: bold; padding: 8px; background: #e9ecef; border-radius: 6px; color: #495057;'>{d}</div>"
            
        for _ in range(start_weekday):
            html += "<div style='padding: 10px;'></div>"

        for day in saved_schedule:
            d_obj = datetime.datetime.strptime(day["Date"], "%Y-%m-%d").date()
            day_str = d_obj.strftime("%b %d") 
            
            html += f"<div style='border: 1px solid #dee2e6; border-radius: 8px; padding: 10px; background: white; min-height: 140px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);'>"
            html += f"<div style='font-weight: bold; border-bottom: 2px solid #f8f9fa; margin-bottom: 8px; padding-bottom: 4px; color: #212529;'>{day_str}</div>"
            
            for shift in day["Shifts"]:
                docs = shift["Physicians"]
                if not docs: docs = "⚠️ UNASSIGNED"
                
                doc_color = "#dc3545" if "⚠️" in docs else "#0056b3"
                doc_weight = "bold" if "⚠️" in docs else "500"

                html += f"<div style='background: #f8f9fa; border-left: 4px solid #007bff; padding: 6px 8px; margin-bottom: 8px; border-radius: 4px;'>"
                html += f"<div style='font-weight: bold; color: #343a40; font-size: 0.9em; margin-bottom: 2px;'>{shift['Name']}</div>"
                html += f"<div style='color: #6c757d; font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.5px;'>📍 {shift['Zone']}</div>"
                html += f"<div style='color: #495057; font-size: 0.8em; margin-bottom: 4px;'>🕒 {shift['Start']} - {shift['End']}</div>"
                html += f"<div style='color: {doc_color}; font-weight: {doc_weight}; font-size: 0.85em;'>👨‍⚕️ {docs}</div>"
                html += f"</div>"

            html += "</div>" 
            
        html += "</div>" 
        st.markdown(html, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Error rendering calendar view: Ensure you generated a new schedule to apply the new data format. ({e})")

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
    tab1, tab2 = st.tabs(["📅 Published Calendar", "🛑 Manage Availability & Preferences"])
    
    with tab1:
        if st.session_state.db_state["saved_schedule"]:
            personal_schedule = []
            for day in st.session_state.db_state["saved_schedule"]:
                personal_shifts = [s for s in day["Shifts"] if st.session_state.current_name in s["Physicians"]]
                if personal_shifts:
                    personal_schedule.append({"Date": day["Date"], "Shifts": personal_shifts})
            render_calendar_view(personal_schedule)
        else:
            st.info("No schedules have been published.")

    with tab2:
        col_ai, col_manual = st.columns([1.2, 1])
        
        with col_ai:
            st.markdown("#### ✨ AI Scheduling Assistant")
            ai_request = st.text_area("Tell Gemini when you need off or what shifts you prefer:", placeholder="e.g., 'I have a dentist appointment on Friday from 1:00 PM to 3:00 PM, and I prefer Day Shifts.'", height=115)
            
            if st.button("Submit Request via AI", type="primary", use_container_width=True):
                api_key = st.session_state.db_state["settings"].get("api_key", "")
                if not api_key:
                    st.error("System AI is currently disabled. Please contact your Administrator to configure the API key.")
                else:
                    with st.spinner("Gemini is processing your availability and preferences..."):
                        try:
                            today_str = datetime.date.today().strftime("%Y-%m-%d")
                            shifts_list = list(st.session_state.shifts_df["Shift Name"].values)
                            parsed_reqs = parse_physician_ai_request(ai_request, api_key, today_str, shifts_list)
                            
                            # Handle Hard Time Off
                            for req in parsed_reqs.get("time_off", []):
                                req["physician"] = st.session_state.current_name
                                st.session_state.db_state["global_unavail"].append(req)
                                
                            # Handle Soft Preferences
                            for pref in parsed_reqs.get("preferences", []):
                                pref["physician_name"] = st.session_state.current_name
                                st.session_state.db_state["global_preferences"].append(pref)
                            
                            save_data(st.session_state.db_state)
                            st.toast("Availability and Preferences saved!", icon="✅")
                            st.rerun()
                        except Exception as e:
                            st.error(f"AI parsing failed. Please check your spelling or try manual entry. ({e})")
                            
            st.divider()
            st.markdown("#### 🌟 Your Active Preferences")
            my_prefs = [p for p in st.session_state.db_state.get("global_preferences", []) if p["physician_name"] == st.session_state.current_name]
            if my_prefs:
                st.dataframe(pd.DataFrame(my_prefs).drop(columns=["physician_name"]), use_container_width=True)
                if st.button("Clear My Preferences", use_container_width=True):
                    st.session_state.db_state["global_preferences"] = [p for p in st.session_state.db_state["global_preferences"] if p["physician_name"] != st.session_state.current_name]
                    save_data(st.session_state.db_state)
                    st.rerun()
            else:
                st.info("You have no active shift preferences.")
        
        with col_manual:
            st.markdown("#### ✍️ Manual Time-Off Entry")
            st.markdown("Use this grid to block specific hours.")
            default_unavail = pd.DataFrame([{"Date": datetime.date.today(), "Start Time": datetime.time(8, 0), "End Time": datetime.time(17, 0)}])
            edited_unavail = st.data_editor(default_unavail, num_rows="dynamic", hide_index=True, use_container_width=True)
            if st.button("Submit Manual Entry", use_container_width=True):
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
                st.toast("Time off saved!", icon="✅")
                st.rerun()
                
            st.divider()
            st.markdown("#### 📋 Your Pending Time-Off")
            my_reqs = [r for r in st.session_state.db_state["global_unavail"] if r["physician"] == st.session_state.current_name]
            if my_reqs:
                st.dataframe(pd.DataFrame(my_reqs).drop(columns=["physician"]), use_container_width=True)
                if st.button("Clear My Time-Off", use_container_width=True):
                    st.session_state.db_state["global_unavail"] = [r for r in st.session_state.db_state["global_unavail"] if r["physician"] != st.session_state.current_name]
                    save_data(st.session_state.db_state)
                    st.rerun()
            else:
                st.info("You have no pending time-off requests.")

# --- 6. ADMIN PORTAL ---
def admin_view():
    with st.sidebar:
        st.success(f"Admin: **{st.session_state.current_name}**")
        if st.button("Log Out", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()
        st.divider()
        
        st.markdown("### ⚙️ Global Parameters")
        s_config = st.session_state.db_state["settings"]
        
        api_key = st.text_input("Gemini API Key:", value=s_config["api_key"], type="password", help="Required for natural language parsing.")
        
        sd_val = datetime.datetime.strptime(s_config["start_date"], "%Y-%m-%d").date()
        ed_val = datetime.datetime.strptime(s_config["end_date"], "%Y-%m-%d").date()
        
        schedule_start_date = st.date_input("Schedule Start Date", value=sd_val)
        schedule_end_date = st.date_input("Schedule End Date", value=ed_val)
        
        if schedule_end_date < schedule_start_date:
            st.error("End date must be after start date.")
            st.stop()
            
        num_days = (schedule_end_date - schedule_start_date).days + 1
        day_offset = schedule_start_date.weekday() 
        min_rest_hours = st.number_input("Mandatory Rest (Hrs)", 0, 48, value=s_config["min_rest_hours"])

    st.header("Admin Command Center")
    tab_config, tab_engine, tab_master = st.tabs(["👥 1. Staff & Shifts", "🧠 2. AI Engine", "📅 3. Master Calendar & Export"])
    
    with tab_config:
        st.markdown("#### ✨ AI Configuration Assistant")
        col_config, col_btn = st.columns([4, 1])
        with col_config:
            config_prompt = st.text_input("Ask AI to add or edit physicians/shifts:", placeholder="e.g., 'Update Dr. Smith to min 10 nights and max 40 total. Add a 12h Fast-Track shift in Triage Zone.'", label_visibility="collapsed")
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
                        save_current_state()
                        st.rerun()
        st.divider()

        st.markdown("#### Provider Contracts (Min/Max Rules)")
        doc_cols = ["Provider ID", "Name", "Min Total", "Max Total", "Min Nights", "Max Nights", "Min Weekends", "Max Weekends"]
        for c in doc_cols:
            if c not in st.session_state.physicians_df.columns:
                st.session_state.physicians_df[c] = 0
        st.session_state.physicians_df = st.session_state.physicians_df[doc_cols]
        
        edited_docs = st.data_editor(st.session_state.physicians_df, num_rows="dynamic", hide_index=True, use_container_width=True, disabled=["Provider ID"])
        needs_update_docs = False
        for idx, row in edited_docs.iterrows():
            if pd.isna(row["Provider ID"]) or str(row["Provider ID"]).strip() == "":
                existing = [int(x.split('-')[1]) for x in edited_docs["Provider ID"].dropna() if '-' in str(x) and str(x).split('-')[1].isdigit()]
                edited_docs.at[idx, "Provider ID"] = f"DOC-{(max(existing) + 1 if existing else 1):02d}"
                needs_update_docs = True
        st.session_state.physicians_df = edited_docs
        if needs_update_docs: 
            save_current_state()
            st.rerun()

        st.markdown("#### Shift Definitions & Zones")
        edited_shifts = st.data_editor(st.session_state.shifts_df, num_rows="dynamic", hide_index=True, use_container_width=True, disabled=["Task ID"])
        needs_update = False
        for idx, row in edited_shifts.iterrows():
            if pd.isna(row["Task ID"]) or str(row["Task ID"]).strip() == "":
                existing = [int(x.split('-')[1]) for x in edited_shifts["Task ID"].dropna() if '-' in str(x) and str(x).split('-')[1].isdigit()]
                edited_shifts.at[idx, "Task ID"] = f"TSK-{(max(existing) + 1 if existing else 1):02d}"
                needs_update = True
        st.session_state.shifts_df = edited_shifts
        if needs_update: 
            save_current_state()
            st.rerun()

        st.divider()
        physicians_list = [r["Name"] for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")]
        valid_carryover = [d for d in s_config["carryover_docs"] if d in physicians_list]
        carryover_docs = st.multiselect("Select providers who worked the final overnight shift of the previous period:", physicians_list, default=valid_carryover)

    st.session_state.db_state["settings"].update({
        "api_key": api_key,
        "start_date": schedule_start_date.strftime("%Y-%m-%d"),
        "end_date": schedule_end_date.strftime("%Y-%m-%d"),
        "min_rest_hours": min_rest_hours,
        "carryover_docs": carryover_docs
    })

    # Dictionaries for solver bounds
    shift_reqs = {r["Shift Name"]: int(r["Req Headcount"]) for _, r in st.session_state.shifts_df.iterrows() if r.get("Shift Name")}
    shift_times = {r["Shift Name"]: {"start": r["Start Time"], "end": r["End Time"]} for _, r in st.session_state.shifts_df.iterrows() if r.get("Shift Name")}
    shift_zones = {r["Shift Name"]: r.get("Zone", "Unspecified") for _, r in st.session_state.shifts_df.iterrows() if r.get("Shift Name")}
    shift_ids = {r["Shift Name"]: r["Task ID"] for _, r in st.session_state.shifts_df.iterrows() if r.get("Shift Name")}
    shifts_list = list(shift_reqs.keys())

    # Max Limits
    p_limits = {r["Name"]: int(r.get("Max Total", 0)) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    n_limits = {r["Name"]: int(r.get("Max Nights", 0)) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    w_limits = {r["Name"]: int(r.get("Max Weekends", 0)) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    
    # Min Limits
    p_min_limits = {r["Name"]: int(r.get("Min Total", 0)) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    n_min_limits = {r["Name"]: int(r.get("Min Nights", 0)) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    w_min_limits = {r["Name"]: int(r.get("Min Weekends", 0)) for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}
    
    p_ids = {r["Name"]: r["Provider ID"] for _, r in st.session_state.physicians_df.iterrows() if r.get("Name")}

    weekend_days = [d for d in range(num_days) if (d + day_offset) % 7 in [5, 6]]
    night_idx = [i for i, s in enumerate(shifts_list) if any(x in s.lower() for x in ["night", "24h", "sick"])]

    with tab_engine:
        col_ai, col_requests = st.columns([2, 1])
        with col_requests:
            req_count = len(st.session_state.db_state["global_unavail"]) + len(st.session_state.db_state.get("global_preferences", []))
            st.metric("Pending Provider Requests & Preferences", req_count)
            with st.expander("Review Provider Submissions"):
                if req_count > 0:
                    st.markdown("**Time Off Requests**")
                    st.dataframe(st.session_state.db_state["global_unavail"], use_container_width=True)
                    st.markdown("**Shift Preferences**")
                    st.dataframe(st.session_state.db_state.get("global_preferences", []), use_container_width=True)
                    
                    if st.button("Clear ALL Requests & Preferences", use_container_width=True):
                        st.session_state.db_state["global_unavail"] = []
                        st.session_state.db_state["global_preferences"] = []
                        save_data(st.session_state.db_state)
                        st.toast("Requests cleared.", icon="🗑️")
                        st.rerun()
                else: st.write("No pending requests.")

        with col_ai:
            st.markdown("#### AI Admin Overrides")
            user_req = st.text_area("Input custom overrides (Admin level):", f"Dr. Patel prefers Day Shifts.", height=150)
            
            if st.button("🚀 Generate Optimal Schedule", type="primary", use_container_width=True):
                save_current_state()
                if not api_key: 
                    st.error("API Key required in sidebar.")
                    st.stop()
                
                # --- PRE-VALIDATION ENGINE ---
                total_shifts_demand = sum(shift_reqs.values()) * num_days
                total_nights_demand = sum(shift_reqs[s] for s in shifts_list if any(x in s.lower() for x in ["night", "24h", "sick"])) * num_days
                total_weekends_demand = sum(shift_reqs.values()) * len(weekend_days)

                total_min_req = sum(p_min_limits.values())
                total_min_nights = sum(n_min_limits.values())
                total_min_weekends = sum(w_min_limits.values())

                errors = []
                for p_name in physicians_list:
                    if p_min_limits.get(p_name, 0) > p_limits.get(p_name, 0): errors.append(f"**{p_name}**: Min Total ({p_min_limits.get(p_name, 0)}) > Max Total ({p_limits.get(p_name, 0)})")
                    if n_min_limits.get(p_name, 0) > n_limits.get(p_name, 0): errors.append(f"**{p_name}**: Min Nights > Max Nights")
                    if w_min_limits.get(p_name, 0) > w_limits.get(p_name, 0): errors.append(f"**{p_name}**: Min Weekends > Max Weekends")

                if total_min_req > total_shifts_demand: errors.append(f"**Global Capacity**: You require a minimum of {total_min_req} total shifts, but only {total_shifts_demand} exist.")
                if total_min_nights > total_nights_demand: errors.append(f"**Global Capacity**: Combined Min Nights ({total_min_nights}) exceeds available night shifts ({total_nights_demand}).")
                if total_min_weekends > total_weekends_demand: errors.append(f"**Global Capacity**: Combined Min Weekends ({total_min_weekends}) exceeds available weekend shifts ({total_weekends_demand}).")

                if errors:
                    st.error("🚨 **Pre-Validation Failed:** Your contractual limits are mathematically impossible. Please fix the Provider Contracts table:")
                    for e in errors: st.markdown(f"- {e}")
                    st.stop()
                
                rules = []
                with st.status("Initializing Engine...", expanded=True) as status:
                    st.write("Translating Admin AI Overrides...")
                    try: rules = parse_constraints(user_req, api_key, physicians_list, shifts_list)
                    except Exception as e: 
                        status.update(label="AI Parsing Failed", state="error")
                        st.error(f"AI Error: {e}"); st.stop()

                    # Merge in Physician-submitted preferences
                    st.write("Injecting Physician Soft Preferences...")
                    for pref in st.session_state.db_state.get("global_preferences", []):
                        rules.append(pref)

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
                    
                    i_min_limits, i_n_min_limits, i_w_min_limits = p_min_limits.copy(), n_min_limits.copy(), w_min_limits.copy()
                    i_min_limits["⚠️ UNASSIGNED GAP"] = i_n_min_limits["⚠️ UNASSIGNED GAP"] = i_w_min_limits["⚠️ UNASSIGNED GAP"] = 0

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
                        model.Add(sum(shifts[(p, d, s)] for d in range(num_days) for s in range(len(shifts_list))) >= i_min_limits[internal_physicians[p]])
                        
                        if night_idx: 
                            model.Add(sum(shifts[(p, d, s)] for d in range(num_days) for s in night_idx) <= i_n_limits[internal_physicians[p]])
                            model.Add(sum(shifts[(p, d, s)] for d in range(num_days) for s in night_idx) >= i_n_min_limits[internal_physicians[p]])
                        
                        if weekend_days: 
                            model.Add(sum(shifts[(p, d, s)] for d in weekend_days for s in range(len(shifts_list))) <= i_w_limits[internal_physicians[p]])
                            model.Add(sum(shifts[(p, d, s)] for d in weekend_days for s in range(len(shifts_list))) >= i_w_min_limits[internal_physicians[p]])

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
                        c_type, t_s = r.get("constraint_type"), shifts_list.index(r["target_shift"]) if r.get("target_shift") in shifts_list else None
                        t_d = None
                        if "target_d" in r: t_d = r["target_d"]
                        elif r.get("target_date"):
                            try: t_d = (datetime.datetime.strptime(r.get("target_date"), "%Y-%m-%d").date() - schedule_start_date).days
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
                            date_str = (schedule_start_date + datetime.timedelta(days=d)).strftime("%Y-%m-%d")
                            day_data = {"Date": date_str, "Shifts": []}
                            for s, s_name in enumerate(shifts_list):
                                docs = [internal_physicians[p] for p in range(len(internal_physicians)) if solver.Value(shifts[(p, d, s)]) == 1]
                                day_data["Shifts"].append({
                                    "Name": s_name,
                                    "Zone": shift_zones.get(s_name, "Unspecified"),
                                    "Start": shift_times[s_name]['start'].strftime("%H:%M"),
                                    "End": shift_times[s_name]['end'].strftime("%H:%M"),
                                    "Physicians": ", ".join(docs)
                                })
                            grid.append(day_data)
                        
                        st.session_state.db_state["saved_schedule"] = grid
                        save_data(st.session_state.db_state)
                        status.update(label="Mathematical Schedule Generated!", state="complete", expanded=False)
                        st.toast("New schedule published to Master view.", icon="🎉")
                    else: 
                        status.update(label="Infeasible Ruleset", state="error")
                        st.error("🚨 Solver Failed: INFEASIBLE. Your minimum shift requirements or time-off requests are mathematically impossible to satisfy. Please lower the minimums or increase the maximums in the Provider Contracts table.")

    with tab_master:
        render_calendar_view(st.session_state.db_state.get("saved_schedule"))

        if st.session_state.db_state.get("saved_schedule"):
            st.divider()
            st.markdown("#### Enterprise Integration")
            flat = []
            for day in st.session_state.db_state["saved_schedule"]:
                for shift in day["Shifts"]:
                    for doc in [d.strip() for d in str(shift["Physicians"]).split(",") if d.strip() and d.strip() != "nan"]:
                        flat.append({
                            "Date": day["Date"],
                            "Provider_ID": p_ids.get(doc, "SYS-GAP"),
                            "Task_ID": shift_ids.get(shift["Name"], "UNKNOWN"),
                            "Zone": shift["Zone"],
                            "Start": shift["Start"],
                            "End": shift["End"]
                        })
            
            st.download_button("📥 Download QGenda / Amion CSV Payload", pd.DataFrame(flat).to_csv(index=False).encode('utf-8'), 'enterprise_export.csv', 'text/csv', type="secondary")

    save_current_state()

# --- 7. ROUTER ---
if not st.session_state.logged_in: login_screen()
elif st.session_state.current_role == "Admin": admin_view()
else: physician_view()
