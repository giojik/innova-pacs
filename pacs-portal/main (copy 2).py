import os, requests, smtplib, io, json, csv, datetime, base64 
from fastapi import FastAPI, Form, Request, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ⚠️ SSL გაფრთხილებების გათიშვა
requests.packages.urllib3.disable_warnings()

app = FastAPI()

# ==========================================================
# 1. გლობალური კონფიგურაცია
# ==========================================================
CLINIC_NAME = "Innova Medical Center"
DOMAIN_NAME = os.getenv("DOMAIN_NAME", "ris.innovamedical.ge")
AE_TITLE = os.getenv("AE_TITLE", "RISINNOVA")
INNOVA_GREEN = "#b1d431"
DARK_BLUE = "#003366"

PACS_URL = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"
KEYCLOAK_TOKEN_URL = "http://keycloak:8080/realms/dcm4che/protocol/openid-connect/token"
CLIENT_ID = "risinnova-ui"

OLD_PACS_RS     = os.getenv("OLD_PACS_URL")
OLD_TOKEN_URL   = os.getenv("OLD_KEYCLOAK_URL")
OLD_CLIENT_ID   = os.getenv("OLD_CLIENT_ID")
OLD_CLIENT_SECRET = os.getenv("OLD_CLIENT_SECRET")

PROGRESS_FILE   = "/app/migrated_uids.txt"
STATUS_FILE     = "/app/migration_status.txt"
ACTIVE_JSON     = "/app/active_transfers.json"
LIVE_STATUS_FILE = "/app/live_sync_status.txt"
LIVE_ACTIVE_JSON = "/app/live_sync_active.json"
SHARE_LOG_FILE  = "/app/share_logs.csv"

SMTP_SERVER = os.getenv("SMTP_SERVER", "relay.mailbaby.net")
SMTP_PORT   = int(os.getenv("SMTP_PORT", 2525))
SMTP_USER   = os.getenv("SMTP_USER", "mb46636")
SMTP_PASS   = os.getenv("SMTP_PASS", "Gv2HmQajcsEkAE2j4nUP")

LANGS = {
    "ka": {
        "title": "ინოვა", "worklist": "სამუშაო სია", "logout": "გამოსვლა",
        "fname": "სახელი", "lname": "გვარი", "id": "პირადი №", "mod": "ტიპი",
        "from": "დან", "to": "მდე", "search": "ძებნა", "total": "სულ ნაპოვნია",
        "birth": "დაბადების თარიღი", "date": "კვლევის თარიღი", "action": "მოქმედება",
        "today": "დღეს", "yesterday": "გუშინ", "week": "კვირა", "month": "თვე", "year": "წელი"
    },
    "en": {
        "title": "Innova", "worklist": "Worklist", "logout": "Logout",
        "fname": "First Name", "lname": "Last Name", "id": "National ID", "mod": "Modality",
        "from": "From", "to": "To", "search": "Search", "total": "Total Found",
        "birth": "Birth Date", "date": "Study Date", "action": "Actions",
        "today": "Today", "yesterday": "Yesterday", "week": "Week", "month": "Month", "year": "Year"
    }
}

# ==========================================================
# 2. ვიზუალური სტილი
# ==========================================================
STYLE = f"""
<style>
    :root {{ --green: {INNOVA_GREEN}; --dark: {DARK_BLUE}; }}
    body {{ font-family: 'Inter', sans-serif; margin: 0; background: #f8fafc; color: #1e293b; overflow-x: hidden; }}

    .login-body {{ background: url('/p/bg.jpg') no-repeat center center fixed; background-size: cover; height: 100vh; display: flex; align-items: center; justify-content: center; }}
    .login-card {{ background: rgba(255,255,255,0.92); backdrop-filter: blur(15px); padding: 3.5rem; border-radius: 50px; width: 380px; box-shadow: 0 25px 50px rgba(0,0,0,0.3); border-top: 10px solid var(--green); text-align: center; }}

    header {{ background: white; padding: 1rem 3rem; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 15px rgba(0,0,0,0.05); position: sticky; top: 0; z-index: 100; }}
    .h-title {{ color: var(--dark); font-weight: 900; font-style: italic; font-size: 1.8rem; margin: 0; text-decoration: none; }}
    .h-title span {{ color: var(--green); }}

    /* ✨ სწრაფი ფილტრების ზოლი */
    .quick-bar {{ max-width: 1450px; margin: 1.2rem auto 0 auto; padding: 0 1.5rem; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .quick-bar-label {{ font-size: 9px; font-weight: 900; color: #94a3b8; text-transform: uppercase; letter-spacing: 2px; margin-right: 4px; white-space: nowrap; }}
    .qf {{ display: inline-flex; align-items: center; gap: 5px; padding: 7px 16px; border-radius: 30px; border: 2px solid #e2e8f0; background: white; color: #475569; font-size: 11px; font-weight: 800; cursor: pointer; text-decoration: none; transition: all 0.18s; white-space: nowrap; }}
    .qf:hover {{ border-color: var(--green); color: var(--green); transform: translateY(-1px); box-shadow: 0 4px 12px rgba(177,212,49,0.2); }}
    .qf.active {{ background: var(--green); border-color: var(--green); color: white; box-shadow: 0 4px 14px rgba(177,212,49,0.35); }}
    .qf.clear {{ border-color: #fca5a5; color: #ef4444; }}
    .qf.clear:hover {{ background: #ef4444; color: white; border-color: #ef4444; }}
    .qf-total {{ margin-left: auto; font-size: 12px; font-weight: 800; color: #94a3b8; }}
    .qf-total b {{ color: var(--dark); }}

    .search-panel {{ background: white; padding: 2rem; border-radius: 40px; box-shadow: 0 10px 30px rgba(0,0,0,0.03); margin: 1rem auto; max-width: 1450px; display: grid; grid-template-columns: repeat(7, 1fr); gap: 12px; align-items: end; border: 1px solid #f1f5f9; position: relative; }}
    .in-input {{ border: none; border-bottom: 2px solid #e2e8f0; padding: 10px 5px; outline: none; transition: 0.3s; font-weight: 700; color: var(--dark); width: 100%; background: transparent; box-sizing: border-box; }}
    .in-input:focus {{ border-bottom-color: var(--green); }}
    label {{ font-size: 9px; font-weight: 800; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; display: block; }}

    .table-wrapper {{ max-width: 1500px; margin: 0 auto 30px auto; padding: 0 1rem; }}
    .table-container {{ background: white; border-radius: 40px; overflow: hidden; box-shadow: 0 20px 40px rgba(0,0,0,0.05); overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 1200px; }}
    thead {{ background: var(--dark); color: white; text-transform: uppercase; font-size: 10px; letter-spacing: 1.2px; }}
    th {{ padding: 1rem 0.9rem; text-align: left; white-space: nowrap; }}
    td {{ padding: 0.85rem 0.9rem; }}
    tbody tr {{ border-bottom: 1px solid #f1f5f9; transition: background 0.15s; }}
    tbody tr:hover {{ background: #f9fdf2; }}

    /* ✨ ახალი სვეტების სტილები */
    .badge-mod {{ background: #e0f2fe; color: #0369a1; padding: 3px 10px; border-radius: 8px; font-weight: 800; font-size: 10px; white-space: nowrap; }}
    .badge-hospital {{ background: #f0fdf4; color: #15803d; padding: 3px 9px; border-radius: 8px; font-size: 10px; font-weight: 700; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; }}
    .badge-hospital.empty {{ background: #f8fafc; color: #cbd5e1; font-style: italic; }}
    .study-desc {{ font-size: 11px; color: #64748b; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }}
    .study-desc.empty {{ color: #cbd5e1; font-style: italic; }}
    .num-cell {{ display: flex; gap: 5px; align-items: center; }}
    .num-pill {{ padding: 3px 9px; border-radius: 8px; font-size: 11px; font-weight: 800; font-family: 'Courier New', monospace; white-space: nowrap; }}
    .num-pill.img {{ background: #fef9c3; color: #92400e; }}
    .num-pill.ser {{ background: #ede9fe; color: #6d28d9; }}

    .btn {{ border: none; padding: 8px 14px; border-radius: 12px; font-weight: 800; text-transform: uppercase; cursor: pointer; transition: 0.2s; font-size: 10px; text-decoration: none; display: inline-block; text-align: center; white-space: nowrap; }}
    .btn-green {{ background: var(--green); color: white; }}
    .btn-dark {{ background: var(--dark); color: white; }}
    .btn-outline {{ border: 2px solid var(--green); color: var(--green); background: transparent; }}
    .btn-share {{ background: #334155; color: white; }}

    .modal {{ display: none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); backdrop-filter: blur(8px); }}
    .modal-content {{ background: white; margin: 8% auto; padding: 3.5rem; border-radius: 50px; width: 450px; text-align: center; border-bottom: 12px solid var(--green); position: relative; box-shadow: 0 30px 60px rgba(0,0,0,0.4); }}
</style>
"""

# ==========================================================
# 3. დამხმარე ფუნქციები
# ==========================================================
def log_share_event(sender, recipient, p_name, p_id, study_date, modality):
    try:
        file_exists = os.path.exists(SHARE_LOG_FILE)
        with open(SHARE_LOG_FILE, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["გაგზავნის დრო", "გამგზავნი ექიმი", "ადრესატი (Email)", "პაციენტი", "პირადი ნომერი", "კვლევის თარიღი", "Modality"])
            writer.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(sender), str(recipient), str(p_name), str(p_id), str(study_date), str(modality)])
    except Exception as e:
        print(f"Logging error: {e}")

def get_name_from_token(token):
    try:
        payload = token.split('.')[1]
        decoded = base64.b64decode(payload + '==').decode('utf-8')
        data = json.loads(decoded)
        return data.get('name', data.get('preferred_username', 'Doctor'))
    except:
        return "Doctor"

def get_old_pacs_token():
    try:
        data = {"client_id": OLD_CLIENT_ID, "client_secret": OLD_CLIENT_SECRET, "grant_type": "client_credentials"}
        r = requests.post(OLD_TOKEN_URL, data=data, timeout=10, verify=False)
        return r.json().get("access_token")
    except:
        return None

def check_server_status(url, is_old=False):
    try:
        headers = {"Accept": "application/json"}
        if is_old:
            token = get_old_pacs_token()
            if not token: return "OFFLINE", "#ef4444"
            headers["Authorization"] = f"Bearer {token}"
            url = f"{url}/studies?limit=1"
        r = requests.get(url, headers=headers, timeout=5, verify=False)
        return ("ONLINE", INNOVA_GREEN) if r.status_code == 200 else ("OFFLINE", "#ef4444")
    except:
        return ("OFFLINE", "#ef4444")

def get_pacs_studies(fname="", lname="", pid="", mod="", d_from="", d_to=""):
    ALLOWED_MODALITIES = ["CT", "MR", "US", "RF", "CR", "ES", "XC", "DX", "XA"]
    try:
        params = {'includefield': 'all', 'limit': 1000, 'fuzzymatching': 'true'}
        f_search = fname.upper().strip()
        l_search = lname.upper().strip()
        if f_search and l_search: params['PatientName'] = f"*{l_search}^{f_search}*"
        elif l_search:             params['PatientName'] = f"*{l_search}*"
        elif f_search:             params['PatientName'] = f"*{f_search}*"
        if pid: params['PatientID'] = pid
        if d_from or d_to:
            start = d_from.replace("-", "") if d_from else "20100101"
            end   = d_to.replace("-", "")   if d_to   else datetime.datetime.now().strftime("%Y%m%d")
            params['StudyDate'] = f"{start}-{end}"
        else:
            params['StudyDate'] = "20200101-"
        r = requests.get(PACS_URL + "/studies", params=params, headers={'Accept': 'application/json'}, timeout=15)
        studies = r.json() if r.status_code == 200 else []
        strict_results = []
        for s in studies:
            m_list = s.get("00080061", {}).get("Value", s.get("00080060", {}).get("Value", []))
            if mod and mod not in m_list: continue
            p_name_raw = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "").upper()
            if f_search and f_search not in p_name_raw: continue
            if l_search and l_search not in p_name_raw: continue
            if any(m in ALLOWED_MODALITIES for m in m_list):
                strict_results.append(s)
        strict_results.sort(key=lambda x: (
            x.get("00080020", {}).get("Value", ["00000000"])[0],
            x.get("00080030", {}).get("Value", ["000000"])[0]
        ), reverse=True)
        return strict_results
    except:
        return []

# ==========================================================
# 4. ავტორიზაცია
# ==========================================================
@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = None):
    err = f'<div style="color:#ef4444;font-weight:bold;font-size:12px;margin-bottom:20px;">{error}</div>' if error else ""
    return f"""<html><head>{STYLE}<title>Login</title></head>
    <body class="login-body">
        <form action="/auth/login" method="post" class="login-card">
            <h1 class="h-title">INNOVA <span>PACS</span></h1>
            <p style="font-size:9px;font-weight:800;color:#94a3b8;letter-spacing:2px;margin:10px 0 40px 0;">AUTHORIZATION</p>
            {err}
            <div style="margin-bottom:20px"><input type="text" name="username" placeholder="Username" class="in-input" required></div>
            <div style="margin-bottom:40px"><input type="password" name="password" placeholder="Password" class="in-input" required></div>
            <button type="submit" class="btn btn-green w-full py-4 text-lg shadow-xl">შესვლა</button>
        </form>
    </body></html>"""

@app.post("/auth/login")
async def auth_login(username: str = Form(...), password: str = Form(...)):
    payload = {'grant_type': 'password', 'client_id': CLIENT_ID, 'username': username, 'password': password}
    try:
        r = requests.post(KEYCLOAK_TOKEN_URL, data=payload, timeout=10)
        if r.status_code == 200:
            token = r.json().get("access_token")
            doc_name = get_name_from_token(token)
            resp = RedirectResponse(url="/doctor/worklist", status_code=303)
            resp.set_cookie(key="doctor_token", value=token, httponly=True, max_age=28800)
            resp.set_cookie(key="doc_full_name", value=doc_name, max_age=28800)
            return resp
        return RedirectResponse(url="/login?error=მონაცემები არასწორია", status_code=303)
    except:
        return RedirectResponse(url="/login?error=კავშირის შეცდომა", status_code=303)

@app.get("/auth/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("doctor_token")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp

# ==========================================================
# 5. ექიმის სამუშაო მაგიდა (Worklist)
# ==========================================================
@app.get("/doctor/worklist", response_class=HTMLResponse)
async def doctor_worklist(
    request: Request,
    fname: str = "", lname: str = "", pid: str = "", mod: str = "",
    d_from: str = "", d_to: str = "", page: int = 1,
    quick: str = "",
    doctor_token: str = Cookie(None),
    doc_full_name: str = Cookie('Doctor')
):
    if not doctor_token: return RedirectResponse(url="/login")

    # ✨ სწრაფი ფილტრების თარიღები
    today = datetime.date.today()
    if quick == "today":
        d_from = d_to = today.strftime("%Y-%m-%d")
    elif quick == "yesterday":
        yest  = today - datetime.timedelta(days=1)
        d_from = d_to = yest.strftime("%Y-%m-%d")
    elif quick == "month":
        d_from = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        d_to   = today.strftime("%Y-%m-%d")
    elif quick == "year":
        d_from = today.replace(month=1, day=1).strftime("%Y-%m-%d")
        d_to   = today.strftime("%Y-%m-%d")

    all_studies = get_pacs_studies(fname, lname, pid, mod, d_from, d_to)

    per_page    = 50
    total_count = len(all_studies)
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    studies     = all_studies[(page - 1) * per_page : page * per_page]

    def get_url(p):
        params = dict(request.query_params)
        params["page"] = p
        return "/doctor/worklist?" + "&".join(f"{k}={v}" for k, v in params.items())

    # სწრაფი ფილტრი — ინარჩუნებს სახელის/pid/mod ძებნას
    def qf_url(q):
        parts = []
        if fname: parts.append(f"fname={fname}")
        if lname: parts.append(f"lname={lname}")
        if pid:   parts.append(f"pid={pid}")
        if mod:   parts.append(f"mod={mod}")
        parts.append(f"quick={q}")
        return "/doctor/worklist?" + "&".join(parts)

    # ✨ ცხრილის სტრიქონები — ახალი DICOM ველებით
    rows = ""
    for s in studies:
        uid      = s.get("0020000D", {}).get("Value", [""])[0]
        raw_name = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "---")
        p_name   = raw_name.replace("^", " ")
        p_id     = s.get("00100020", {}).get("Value", ["---"])[0]
        p_dob    = s.get("00100030", {}).get("Value", ["---"])[0]
        s_date   = s.get("00080020", {}).get("Value", ["---"])[0]
        mod_list = s.get("00080061", {}).get("Value", s.get("00080060", {}).get("Value", ["---"]))
        s_mod    = ", ".join(mod_list)

        # ✨ ახალი ველები
        hospital   = ((s.get("00080080",  {}).get("Value") or [""])[0] or "").strip() or "—"
        study_desc = ((s.get("00081030",  {}).get("Value") or [""])[0] or "").strip() or "—"
        num_images = str((s.get("00201208", {}).get("Value") or ["—"])[0])
        num_series = str((s.get("00201206", {}).get("Value") or ["—"])[0])

        f_date     = f"{s_date[:4]}-{s_date[4:6]}-{s_date[6:8]}" if len(s_date) == 8 else s_date
        f_dob      = f"{p_dob[:4]}-{p_dob[4:6]}-{p_dob[6:8]}"   if len(p_dob)  == 8 else p_dob
        safe_name  = p_name.replace("'", "\\'")

        hosp_cls = "" if hospital   != "—" else " empty"
        desc_cls = "" if study_desc != "—" else " empty"

        rows += f"""<tr>
            <td style="font-size:11px;color:#64748b;">{p_id}</td>
            <td style="font-weight:700;color:var(--dark);">{p_name}</td>
            <td style="font-size:11px;">{f_dob}</td>
            <td><span class="badge-mod">{s_mod}</span></td>
            <td style="font-weight:800;color:var(--dark);font-size:12px;">{f_date}</td>
            <td><span class="badge-hospital{hosp_cls}" title="{hospital}">{hospital}</span></td>
            <td><span class="study-desc{desc_cls}" title="{study_desc}">{study_desc}</span></td>
            <td>
                <div class="num-cell">
                    <span class="num-pill img" title="Images">🖼 {num_images}</span>
                    <span class="num-pill ser" title="Series">📁 {num_series}</span>
                </div>
            </td>
            <td style="text-align:right;">
                <div style="display:flex;gap:6px;justify-content:flex-end;">
                    <a href="/viewer?StudyInstanceUIDs={uid}" target="_blank" class="btn btn-green">View</a>
                    <a href="/p/download-zip/{uid}/auto" class="btn btn-outline">ZIP</a>
                    <button onclick="openShare('{uid}','{safe_name}','{p_id}','{f_dob}','{f_date}','{s_mod}')" class="btn btn-share">Share</button>
                </div>
            </td>
        </tr>"""

    pagination = f"""
    <div style="display:flex;justify-content:center;align-items:center;gap:25px;margin:30px 0 50px 0;">
        <a href="{get_url(page-1)}" class="btn btn-dark" style="{'opacity:0.25;pointer-events:none' if page<=1 else ''}">⬅ Prev</a>
        <span style="font-weight:900;color:var(--dark);">გვერდი {page} / {total_pages}</span>
        <a href="{get_url(page+1)}" class="btn btn-dark" style="{'opacity:0.25;pointer-events:none' if page>=total_pages else ''}">Next ➡</a>
    </div>"""

    # ✨ სწრაფი ფილტრების ზოლი
    clear_btn = f'<a href="/doctor/worklist" class="qf clear">✕ გასუფთავება</a>' if quick else ""
    quick_bar = f"""
    <div class="quick-bar">
        <span class="quick-bar-label">⚡ სწრაფი:</span>
        <a href="{qf_url('today')}"     class="qf {'active' if quick=='today'     else ''}">📅 დღეს</a>
        <a href="{qf_url('yesterday')}" class="qf {'active' if quick=='yesterday' else ''}">🌙 გუშინ</a>
        <a href="{qf_url('month')}"     class="qf {'active' if quick=='month'     else ''}">📆 ბოლო 30 დღე</a>
        <a href="{qf_url('year')}"      class="qf {'active' if quick=='year'      else ''}">🗓 ამ წელს</a>
        {clear_btn}
        <span class="qf-total">სულ: <b>{total_count}</b> კვლევა</span>
    </div>"""

    html = f"""<html><head>{STYLE}
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <title>Worklist</title></head>
    <body>
        <header>
            <a href="/doctor/worklist" class="h-title">INNOVA <span>PACS</span></a>
            <div style="display:flex;gap:20px;align-items:center;">
                <span style="font-size:12px;color:#94a3b8;font-weight:bold;text-transform:uppercase;">
                    მოგესალმებით, <b style="color:#003366;">{doc_full_name}</b>
                </span>
                <a href="/auth/logout" class="btn btn-dark">LOGOUT</a>
            </div>
        </header>

        {quick_bar}

        <form class="search-panel">
            <a href="/doctor/worklist" style="position:absolute;top:-10px;right:-10px;background:#ef4444;color:white;width:28px;height:28px;border-radius:50%;text-align:center;line-height:28px;text-decoration:none;font-weight:bold;box-shadow:0 4px 10px rgba(239,68,68,0.3);">✕</a>
            <div><label>First Name</label><input type="text" name="fname" value="{fname}" class="in-input"></div>
            <div><label>Last Name</label><input type="text" name="lname" value="{lname}" class="in-input"></div>
            <div><label>National ID</label><input type="text" name="pid" value="{pid}" class="in-input"></div>
            <div><label>Modality</label>
                <select name="mod" class="in-input">
                    <option value="">ALL</option>
                    <option value="CT"  {'selected' if mod=='CT'  else ''}>CT</option>
                    <option value="MR"  {'selected' if mod=='MR'  else ''}>MR</option>
                    <option value="US"  {'selected' if mod=='US'  else ''}>US</option>
                    <option value="CR"  {'selected' if mod=='CR'  else ''}>CR/DX</option>
                    <option value="RF"  {'selected' if mod=='RF'  else ''}>RF</option>
                    <option value="ES"  {'selected' if mod=='ES'  else ''}>ენდოსკოპია (ES)</option>
                    <option value="DX"  {'selected' if mod=='DX'  else ''}>დენსიტომეტრია (DX)</option>
                    <option value="XC"  {'selected' if mod=='XC'  else ''}>XC</option>
                    <option value="XA"  {'selected' if mod=='XA'  else ''}>XA</option>
                </select>
            </div>
            <div><label>From</label><input type="date" name="d_from" value="{d_from}" class="in-input"></div>
            <div><label>To</label><input type="date" name="d_to"   value="{d_to}"   class="in-input"></div>
            <button type="submit" class="btn btn-green" style="height:42px;">SEARCH</button>
        </form>

        <div class="table-wrapper">
            <div class="table-container">
                <table>
                    <thead><tr>
                        <th>ID</th>
                        <th>პაციენტი</th>
                        <th>დაბ. თარიღი</th>
                        <th>Modality</th>
                        <th>კვლევის თარიღი</th>
                        <th>Institution Name</th>
                        <th>Study Description</th>
                        <th>Images / Series</th>
                        <th style="text-align:right;">Actions</th>
                    </tr></thead>
                    <tbody>
                        {rows if rows else '<tr><td colspan="9" style="padding:80px;text-align:center;color:#94a3b8;font-style:italic;">No records found</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        {pagination}

        <div id="shareModal" class="modal">
            <div class="modal-content">
                <h2 id="modalName" style="color:var(--dark);font-weight:900;margin-bottom:25px;">Share Study</h2>
                <div style="background:#f8fafc;padding:20px;border-radius:25px;margin-bottom:20px;border:1px solid #eee;">
                    <label style="text-align:left;color:#64748b;">SEND VIA EMAIL</label>
                    <input type="email" id="shareEmail" placeholder="Patient Email" class="in-input" style="border:2px solid #e2e8f0;border-radius:15px;padding:12px;margin:10px 0;">
                    <button onclick="sendEmail(this)" class="btn btn-green" style="width:100%;padding:12px;">Send Email</button>
                </div>
                <p style="color:#cbd5e1;font-weight:bold;font-size:10px;">OR</p>
                <div style="margin-top:20px;">
                    <button onclick="printQR()" class="btn btn-dark" style="width:100%;padding:15px;background:#334155;">🖨️ Print QR Instructions</button>
                </div>
                <button onclick="document.getElementById('shareModal').style.display='none'" style="margin-top:30px;background:none;border:none;color:#94a3b8;font-weight:800;cursor:pointer;text-transform:uppercase;font-size:10px;">Close Window</button>
            </div>
        </div>

        <script>
        let shareData = {{}};
        const docName = "{doc_full_name}";

        function openShare(uid, name, pid, dob, s_date, mod) {{
            shareData = {{ uid, name, pid, dob, s_date, mod }};
            document.getElementById('modalName').innerText = name;
            document.getElementById('shareModal').style.display = 'block';
            document.getElementById('shareEmail').value = '';
        }}

        async function sendEmail(btn) {{
            const email = document.getElementById('shareEmail').value;
            if (!email || !email.includes('@')) return alert('შეიყვანეთ ვალიდური იმეილი');
            btn.innerText = 'იგზავნება...';
            btn.disabled = true;
            const fd = new FormData();
            fd.append('email', email);
            fd.append('p_name', shareData.name);
            fd.append('p_id', shareData.pid);
            fd.append('study_date', shareData.s_date);
            fd.append('modality', shareData.mod);
            fd.append('sender_name', docName);
            fd.append('body', 'https://{DOMAIN_NAME}/p/' + shareData.uid);
            try {{
                const res = await fetch('/p/send-email', {{ method: 'POST', body: fd }});
                if (res.ok) {{
                    alert('მეილი წარმატებით გაიგზავნა!');
                    document.getElementById('shareModal').style.display = 'none';
                }} else alert('შეცდომა გაგზავნისას');
            }} catch(e) {{ alert('კავშირის შეცდომა'); }}
            finally {{ btn.innerText = 'Send Email'; btn.disabled = false; }}
        }}

        function printQR() {{
            const w = window.open('', '_blank');
            const url = "https://{DOMAIN_NAME}/p/" + shareData.uid;
            const qrHtml = '<html><head><script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></' + 'script><style>body{{font-family:sans-serif;text-align:center;padding:40px;}}.card{{border:2px dashed #b1d431;padding:30px;border-radius:20px;max-width:400px;margin:auto;}}#q{{display:flex;justify-content:center;margin:20px 0;}}</style></head><body><div class="card"><h2>Innova Medical Center</h2><p>Patient: <b>' + shareData.name + '</b><br>ID: ' + shareData.pid + '</p><div id="q"></div><p>დაასკანერეთ კვლევის სანახავად</p></div><script>new QRCode(document.getElementById("q"),{{text:"' + url + '",width:180,height:180}});setTimeout(()=>window.print(),800);</' + 'script></body></html>';
            w.document.write(qrHtml);
            w.document.close();
        }}
        </script>
    </body></html>"""

    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, must-revalidate"})

# ==========================================================
# 6. ZIP გადმოწერა
# ==========================================================
@app.get("/p/download-zip/{uid}/auto")
async def download_zip(uid: str):
    final_name = f"Innova_Study_{uid[:8]}.zip"
    try:
        m_res = requests.get(f"{PACS_URL}/studies?StudyInstanceUID={uid}", headers={'Accept': 'application/json'}, timeout=5)
        if m_res.status_code == 200 and m_res.json():
            m        = m_res.json()[0]
            date     = m.get("00080020", {}).get("Value", ["00000000"])[0]
            pid      = m.get("00100020", {}).get("Value", ["000"])[0]
            mod_list = m.get("00080061", {}).get("Value", m.get("00080060", {}).get("Value", ["STUDY"]))
            mod      = str(mod_list[0]).replace("/", "-")
            raw_name = m.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "P^Name")
            if "^" in raw_name:
                parts      = raw_name.split("^")
                surname    = parts[0].strip()
                first_name = parts[1].strip() if len(parts) > 1 else ""
                initial    = first_name[0].upper() if first_name else "P"
                name_part  = f"{initial}_{surname}"
            else:
                name_part = raw_name.replace(" ", "_")
            final_name = f"{date}_{name_part}_{mod}_{pid}.zip"
    except Exception as e:
        print(f"⚠️ Filename error: {e}")

    pacs_url = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs/studies/{uid}?accept=application/zip&dicomdir=true"

    def iterfile():
        with requests.get(pacs_url, stream=True, timeout=None) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{final_name}"'}
    )

# ==========================================================
# 7. კვლევის გაგზავნა ელ-ფოსტაზე
# ==========================================================
@app.post("/p/send-email")
async def send_email(
    email:       str = Form(...),
    body:        str = Form(...),
    p_name:      str = Form("Unknown"),
    p_id:        str = Form("---"),
    study_date:  str = Form("---"),
    modality:    str = Form("---"),
    sender_name: str = Form("Innova PACS")
):
    try:
        def _s(v): return str(v[0] if isinstance(v, (list, tuple)) else v).strip()
        email_final   = _s(email)
        patient_final = _s(p_name)
        body_final    = _s(body)
        sender_final  = _s(sender_name)
        date_final    = _s(study_date)
        mod_final     = _s(modality)
        p_id_final    = _s(p_id)

        msg = MIMEMultipart('alternative')
        msg['From']    = "Innova Medical Center <pacs@innovamedical.ge>"
        msg['To']      = email_final
        msg['Subject'] = "თქვენი სამედიცინო კვლევის პასუხი"
        html_content = f"""
        <html><body style="font-family:sans-serif;color:#333;">
            <div style="max-width:600px;margin:auto;border:1px solid #eee;padding:30px;border-radius:15px;">
                <h2 style="color:#004a99;text-align:center;">Innova Medical Center</h2>
                <p>მოგესალმებით, თქვენი კვლევის შედეგები ხელმისაწვდომია პორტალზე.</p>
                <div style="background:#f8f9fa;padding:15px;border-radius:10px;margin:20px 0;">
                    <p>პაციენტი: <b>{patient_final}</b><br>პირადი ნომერი: <b>{p_id_final}</b></p>
                </div>
                <div style="text-align:center;">
                    <a href="{body_final}" target="_blank" style="background:#004a99;color:white;padding:15px 25px;text-decoration:none;border-radius:8px;font-weight:bold;">კვლევის ნახვა</a>
                </div>
            </div>
        </body></html>"""
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail("pacs@innovamedical.ge", email_final, msg.as_string())
        server.quit()

        log_share_event(sender_final, email_final, patient_final, p_id_final, date_final, mod_final)
        print(f"✅ გაეგზავნა: {patient_final}")
        return {"status": "success"}

    except Exception as e:
        print(f"❌ SMTP Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================================
# 8. პაციენტის პორტალი
# ==========================================================
@app.get("/p/{study_uid}", response_class=HTMLResponse)
async def patient_login_page(study_uid: str):
    html = f"""<html><head>{STYLE}<title>პაციენტის პორტალი</title></head>
    <body class="login-body">
        <form action="/p/verify" method="post" class="login-card">
            <h1 class="h-title">INNOVA <span>PACS</span></h1>
            <p style="font-size:9px;font-weight:800;color:#94a3b8;letter-spacing:2px;margin:10px 0 30px 0;">PATIENT ACCESS</p>
            <input type="hidden" name="uid" value="{study_uid}">
            <div style="margin-bottom:15px;text-align:left;">
                <label>პირადი ნომერი</label>
                <input type="text" name="pid" placeholder="11 ნიშნა კოდი" class="in-input" required>
            </div>
            <div style="margin-bottom:30px;text-align:left;">
                <label>დაბადების თარიღი</label>
                <input type="text" name="dob" placeholder="მაგ: წელი/თვე/რიცხვი" class="in-input" required>
            </div>
            <button type="submit" class="btn btn-green w-full py-4 text-lg shadow-xl">კვლევის ნახვა</button>
            <p style="font-size:10px;color:#999;margin-top:20px;font-style:italic;">გთხოვთ შეიყვანოთ მონაცემები კვლევის გასახსნელად</p>
        </form>
    </body></html>"""
    return HTMLResponse(content=html)

@app.post("/p/verify")
async def verify_patient(uid: str = Form(...), pid: str = Form(...), dob: str = Form(...)):
    try:
        r = requests.get(
            f"{PACS_URL}/studies",
            params={"StudyInstanceUID": uid},
            headers={"Accept": "application/json"},
            timeout=10
        )
        if r.status_code == 200 and r.json():
            data     = r.json()[0]
            real_pid = str(data.get("00100020", {}).get("Value", [""])[0]).strip()
            real_dob = str(data.get("00100030", {}).get("Value", [""])[0]).strip()
            u_pid    = "".join(filter(str.isdigit, pid))
            u_dob    = "".join(filter(str.isdigit, dob))
            if u_pid == real_pid and u_dob == real_dob:
                resp = RedirectResponse(url=f"/viewer?StudyInstanceUIDs={uid}", status_code=303)
                resp.set_cookie(key="patient_auth", value=uid, max_age=3600, path="/", domain=DOMAIN_NAME, secure=True)
                return resp
        return HTMLResponse("<script>alert('მონაცემები არასწორია'); window.history.back();</script>")
    except Exception as e:
        return HTMLResponse(f"ვერიფიკაციის შეცდომა: {str(e)}")
