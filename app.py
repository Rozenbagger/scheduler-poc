import streamlit as st
import pandas as pd
from google import genai
import json
import datetime
from ortools.sat.python import cp_model

# --- 1. SETUP & SESSION STATE ---
st.set_page_config(page_title="Shift Command v22", page_icon="📅", layout="wide")

if "shifts_df" not in st.session_state:
    st.session_state.shifts_df = pd.DataFrame([
        {"Shift Name": "Day Shift", "Start": "07:00", "End": "15:00", "Count": 2},
        {"Shift Name": "Night Shift", "Start": "23:00", "End": "07:00", "Count": 1}
    ])

if "physicians_df" not in st.session_state:
    st.session_state.physicians_df = pd.DataFrame([
        {"Name": "Dr. Smith", "Max Shifts": 15},
        {"Name": "Dr. Jones", "Max Shifts": 15},
        {"Name": "Dr. Patel", "Max Shifts": 20}
    ])

if "schedule_results" not in st.session_state:
    st.session_state.schedule_results = None

if "sync_key" not in st.session_state:
    st.session_state.sync_key = 0

# --- 2. SIDEBAR (CORE TOOLS) ---
with st.sidebar:
    st.header("⚙️ Schedule Controls")
    start_date = st.date_input("Calendar Start", datetime.date.today())
    num_days = st.slider("Duration (Days)", 7, 31, 14)
    min_rest = st.number_input("Min Rest (Hours)", 0, 24, 12)
    
    st.divider()
    api_key = st.text_input("Gemini API Key", type="password")
    
    if st.button("Clear All Data"):
        st.session_state.clear()
        st.rerun()

# --- 3. AI DATA ENTRY ENGINE ---
def run_ai_v22(text, key):
    client = genai.Client(api_key=key)
    prompt = f"""
    Extract data from: "{text}". 
    Return ONLY JSON: {{ "type": "shift" or "physician", "data": {{ ... }} }}
    For shifts: Name, Start (HH:MM), End (HH:MM), Count (int).
    For physicians: Name, Max Shifts (int).
    """
    response = client.models.generate_content(
        model='gemini-2.0-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    return json.loads(response.text)

# --- 4. MAIN INTERFACE ---
st.title("🏥 Medical Scheduler v22")

st.subheader("🤖 AI Command")
user_input = st.text_input("Add data:", placeholder="e.g. 'Add a 12-hour Swing shift 10:00 to 22:00 with 2 people'")

if st.button("Update Tables") and api_key:
    try:
        res = run_ai_v22(user_input, api_key)
        if res["type"] == "shift":
            st.session_state.shifts_df = pd.concat([st.session_state.shifts_df, pd.DataFrame([res["data"]])], ignore_index=True)
        else:
            st.session_state.physicians_df = pd.concat([st.session_state.physicians_df, pd.DataFrame([res["data"]])], ignore_index=True)
        st.session_state.sync_key += 1
        st.rerun()
    except Exception as e:
        st.error(f"AI Parse Error: {e}")

col1, col2 = st.columns(2)
with col1:
    st.write("### 🕒 Shift Definitions")
    st.session_state.shifts_df = st.data_editor(st.session_state.shifts_df, key=f"s_{st.session_state.sync_key}", hide_index=True, num_rows="dynamic")
with col2:
    st.write("### 👨‍⚕️ Physician Roster")
    st.session_state.physicians_df = st.data_editor(st.session_state.physicians_df, key=f"p_{st.session_state.sync_key}", hide_index=True, num_rows="dynamic")

# --- 5. THE SOLVER & VISUAL CALENDAR ---
st.divider()
if st.button("🚀 Generate Visual Schedule", type="primary"):
    # --- Solver Logic (Simplified v22 Engine) ---
    model = cp_model.CpModel()
    num_physicians = len(st.session_state.physicians_df)
    num_shifts = len(st.session_state.shifts_df)
    
    # Assignments[day, shift, physician]
    assign = {}
    for d in range(num_days):
        for s in range(num_shifts):
            for p in range(num_physicians):
                assign[(d, s, p)] = model.NewBoolVar(f'shift_{d}_{s}_{p}')

    # Rule: Meet headcount for each shift
    for d in range(num_days):
        for s in range(num_shifts):
            model.Add(sum(assign[(d, s, p)] for p in range(num_physicians)) == int(st.session_state.shifts_df.iloc[s]['Count']))

    # Rule: Physician Max Shifts
    for p in range(num_physicians):
        model.Add(sum(assign[(d, s, p)] for d in range(num_days) for s in range(num_shifts)) <= int(st.session_state.physicians_df.iloc[p]['Max Shifts']))

    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        schedule_data = []
        for d in range(num_days):
            current_date = start_date + datetime.timedelta(days=d)
            day_info = {"Date": current_date.strftime("%m/%d (%a)")}
            for s in range(num_shifts):
                shift_name = st.session_state.shifts_df.iloc[s]['Shift Name']
                assigned = [st.session_state.physicians_df.iloc[p]['Name'] for p in range(num_physicians) if solver.Value(assign[(d, s, p)])]
                day_info[shift_name] = ", ".join(assigned)
            schedule_data.append(day_info)
        st.session_state.schedule_results = pd.DataFrame(schedule_data)
    else:
        st.error("No valid schedule found. Try relaxing Max Shift constraints.")

# RENDER THE VISUAL GRID
if st.session_state.schedule_results is not None:
    st.subheader("🗓️ Master Schedule")
    
    # Custom styling for the Visual Grid
    def color_assignments(val):
        if not val: return 'background-color: #ffcccc' # Red for gaps
        return 'background-color: #e6f3ff' # Blue for filled
    
    st.dataframe(
        st.session_state.schedule_results.style.applymap(color_assignments, subset=st.session_state.schedule_results.columns[1:]),
        use_container_width=True,
        hide_index=True
    )
