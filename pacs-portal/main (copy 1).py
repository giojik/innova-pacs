import os, requests, smtplib, io, json, csv, datetime, base64 
from fastapi import FastAPI, Form, Request, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ⚠️ SSL გაფრთხილებების გათიშვა (შიდა ქსელში სერტიფიკატების პრობლემის ასაცილებლად)
requests.packages.urllib3.disable_warnings()

app = FastAPI()

# ==========================================================
# 1. გლობალური კონფიგურაცია (პარამეტრები .env ფაილიდან)
# ==========================================================
CLINIC_NAME = "Innova Medical Center"
DOMAIN_NAME = os.getenv("DOMAIN_NAME", "ris.innovamedical.ge")
AE_TITLE = os.getenv("AE_TITLE", "RISINNOVA")
INNOVA_GREEN = "#b1d431" # ინოვას საფირმო მწვანე
DARK_BLUE = "#003366"    # მუქი ლურჯი აქცენტებისთვის

# მისამართები (PACS და Keycloak)
PACS_URL = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"
KEYCLOAK_TOKEN_URL = "http://keycloak:8080/realms/dcm4che/protocol/openid-connect/token"
CLIENT_ID = "risinnova-ui" 

# ძველი PACS-ის მონაცემები (მიგრაციისთვის)
OLD_PACS_RS = os.getenv("OLD_PACS_URL")
OLD_TOKEN_URL = os.getenv("OLD_KEYCLOAK_URL")
OLD_CLIENT_ID = os.getenv("OLD_CLIENT_ID")
OLD_CLIENT_SECRET = os.getenv("OLD_CLIENT_SECRET")

# ფაილების გზები (სტატუსებისა და ლოგებისთვის)
PROGRESS_FILE = "/app/migrated_uids.txt"      # გადმოტანილი კვლევების სია
STATUS_FILE = "/app/migration_status.txt"      # მიგრაციის ჩართვა/გამორთვა
ACTIVE_JSON = "/app/active_transfers.json"    # მიმდინარე ტრანსფერები
LIVE_STATUS_FILE = "/app/live_sync_status.txt" # სინქრონიზაციის სტატუსი
LIVE_ACTIVE_JSON = "/app/live_sync_active.json" # სინქრონიზაციის პროგრესი
SHARE_LOG_FILE = "/app/share_logs.csv"         # გაზიარების ისტორია

# იმეილის სერვერის (SMTP) პარამეტრები
SMTP_SERVER = os.getenv("SMTP_SERVER", "relay.mailbaby.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", 2525))
SMTP_USER = os.getenv("SMTP_USER", "mb46636")
SMTP_PASS = os.getenv("SMTP_PASS", "Gv2HmQajcsEkAE2j4nUP")

#ლექსიკონი
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
# 2. ვიზუალური სტილი (CSS - ჩაშენებული კოდში)
# ==========================================================
STYLE = f"""
<style>
    :root {{ --green: {INNOVA_GREEN}; --dark: {DARK_BLUE}; }}
    body {{ font-family: 'Inter', sans-serif; margin: 0; background: #f8fafc; color: #1e293b; overflow-x: hidden; }}
    
    /* ლოგინის გვერდის დიზაინი */
    .login-body {{ background: url('/p/bg.jpg') no-repeat center center fixed; background-size: cover; height: 100vh; display: flex; align-items: center; justify-content: center; }}
    .login-card {{ background: rgba(255, 255, 255, 0.92); backdrop-filter: blur(15px); padding: 3.5rem; border-radius: 50px; width: 380px; box-shadow: 0 25px 50px rgba(0,0,0,0.3); border-top: 10px solid var(--green); text-align: center; }}
    
    /* გვერდის ზედა ზოლი (Header) */
    header {{ background: white; padding: 1rem 3rem; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 15px rgba(0,0,0,0.05); }}
    .h-title {{ color: var(--dark); font-weight: 900; font-style: italic; font-size: 1.8rem; margin: 0; text-decoration:none; }}
    .h-title span {{ color: var(--green); }}
    
    /* საძიებო პანელი */
    .search-panel {{ background: white; padding: 2rem; border-radius: 40px; box-shadow: 0 10px 30px rgba(0,0,0,0.03); margin: 2rem auto; max-width: 1450px; display: grid; grid-template-columns: repeat(7, 1fr); gap: 12px; align-items: end; border: 1px solid #f1f5f9; position: relative; }}
    .in-input {{ border: none; border-bottom: 2px solid #e2e8f0; padding: 10px 5px; outline: none; transition: 0.3s; font-weight: 700; color: var(--dark); width: 100%; background: transparent; box-sizing: border-box; }}
    .in-input:focus {{ border-bottom-color: var(--green); }}
    label {{ font-size: 9px; font-weight: 800; color: #94a3b8; text-transform: uppercase; margin-bottom: 5px; display: block; }}
    
    /* ცხრილის სტილები */
    .table-container {{ max-width: 1500px; margin: 0 auto 50px auto; background: white; border-radius: 40px; overflow: hidden; box-shadow: 0 20px 40px rgba(0,0,0,0.05); }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead {{ background: var(--dark); color: white; text-transform: uppercase; font-size: 11px; letter-spacing: 1.5px; }}
    th, td {{ padding: 1.2rem; text-align: left; }}
    tr {{ border-bottom: 1px solid #f8fafc; transition: 0.2s; }}
    tr:hover {{ background: #f9fdf2; }}
    
    /* ღილაკები */
    .btn {{ border: none; padding: 10px 18px; border-radius: 14px; font-weight: 800; text-transform: uppercase; cursor: pointer; transition: 0.3s; font-size: 10px; text-decoration: none; display: inline-block; text-align: center; }}
    .btn-green {{ background: var(--green); color: white; }}
    .btn-dark {{ background: var(--dark); color: white; }}
    .btn-outline {{ border: 2px solid var(--green); color: var(--green); background:transparent; }}
    .btn-share {{ background: #334155; color: white; }}
    
    /* მოდალური ფანჯარა (Share Popup) */
    .modal {{ display: none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); backdrop-filter: blur(8px); }}
    .modal-content {{ background: white; margin: 8% auto; padding: 3.5rem; border-radius: 50px; width: 450px; text-align: center; border-bottom: 12px solid var(--green); position: relative; box-shadow: 0 30px 60px rgba(0,0,0,0.4); }}
</style>
"""

# ==========================================================
# 3. დამხმარე ფუნქციები (ბექენდ ლოგიკა)
# ==========================================================
#გაზიარების ლოგები
def log_share_event(sender, recipient, p_name, p_id, study_date, modality):
    try:
        file_path = SHARE_LOG_FILE
        file_exists = os.path.exists(file_path)
        
        # ვიყენებთ utf-8-sig ფორმატს, რომ Excel-მა ქართული ასოები სწორად დაინახოს
        with open(file_path, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if not file_exists:
                # სვეტების სათაურები
                writer.writerow(["გაგზავნის დრო", "გამგზავნი ექიმი", "ადრესატი (Email)", "პაციენტი", "პირადი ნომერი", "კვლევის თარიღი", "Modality"])
            
            writer.writerow([
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), # გაგზავნის დრო
                str(sender), 
                str(recipient), 
                str(p_name), 
                str(p_id), 
                str(study_date), 
                str(modality)
            ])
    except Exception as e:
        print(f"Logging error: {e}")
#ექიმის სახელის ამოღება
def get_name_from_token(token):
    try:
        # JWT ტოკენის შუა ნაწილის დეკოდირება (Payload)
        payload = token.split('.')[1]
        decoded = base64.b64decode(payload + '==').decode('utf-8')
        data = json.loads(decoded)
        return data.get('name', data.get('preferred_username', 'Doctor'))
    except: return "Doctor"
# ძველი PACS-ისთვის წვდომის ტოკენის აღება Keycloak-იდან
def get_old_pacs_token():
    try:
        data = {"client_id": OLD_CLIENT_ID, "client_secret": OLD_CLIENT_SECRET, "grant_type": "client_credentials"}
        r = requests.post(OLD_TOKEN_URL, data=data, timeout=10, verify=False)
        return r.json().get("access_token")
    except: return None
# სერვერების სტატუსის შემოწმება (Online/Offline)
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
    except: return ("OFFLINE", "#ef4444")
# ძიების მთავარი ფუნქცია (PACS QIDO-RS მოთხოვნა)
def get_pacs_studies(fname="", lname="", pid="", mod="", d_from="", d_to=""):
    ALLOWED_MODALITIES = ["CT", "MR", "US", "RF", "CR", "ES", "XC", "DX"]
    try:
        # ზღვარს ვზრდით 1000-მდე, რომ პაგინაციამ ბევრ გვერდზე იმუშაოს
        params = {'includefield': 'all', 'limit': 1000, 'fuzzymatching': 'true'}
        
        f_search = fname.upper().strip()
        l_search = lname.upper().strip()
        
        if f_search and l_search: params['PatientName'] = f"*{l_search}^{f_search}*"
        elif l_search: params['PatientName'] = f"*{l_search}*"
        elif f_search: params['PatientName'] = f"*{f_search}*"
        
        if pid: params['PatientID'] = pid
        
        # თარიღის ლოგიკა
        if d_from or d_to:
            start = d_from.replace("-", "") if d_from else "20100101"
            end = d_to.replace("-", "") if d_to else datetime.datetime.now().strftime("%Y%m%d")
            params['StudyDate'] = f"{start}-{end}"
        else:
            # ⚡ აი აქ შეიცვალა: თუ ფილტრი ცარიელია, ეძებს 2020 წლიდან დღემდე (ანუ ყველას)
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

        # ⚡ სორტირება: უახლესი ყოველთვის ზემოთ (თარიღი + დრო)
        strict_results.sort(key=lambda x: (
            x.get("00080020", {}).get("Value", ["00000000"])[0], 
            x.get("00080030", {}).get("Value", ["000000"])[0]
        ), reverse=True)
        
        return strict_results
    except:
        return []
    
# ==========================================================
# 4. ავტორიზაცია (Login, Logout)
# ==========================================================
#ავტორიზაციის გვერდი
@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = None):
    # ლოგინის გვერდის ჩვენება
    err = f'<div style="color:#ef4444; font-weight:bold; font-size:12px; margin-bottom:20px;">{error}</div>' if error else ""
    return f"""<html><head>{STYLE}<title>Login</title></head>
    <body class="login-body">
        <form action="/auth/login" method="post" class="login-card">
            <h1 class="h-title">INNOVA <span>PACS</span></h1>
            <p style="font-size:9px; font-weight:800; color:#94a3b8; letter-spacing:2px; margin: 10px 0 40px 0;">AUTHORIZATION</p>
            {err}
            <div style="margin-bottom:20px"><input type="text" name="username" placeholder="Username" class="in-input" required></div>
            <div style="margin-bottom:40px"><input type="password" name="password" placeholder="Password" class="in-input" required></div>
            <button type="submit" class="btn btn-green w-full py-4 text-lg shadow-xl">შესვლა</button>
        </form>
    </body></html>"""
#ავტორიზაციის ფუნქცია
@app.post("/auth/login")
async def auth_login(username: str = Form(...), password: str = Form(...)):
    payload = {'grant_type': 'password', 'client_id': CLIENT_ID, 'username': username, 'password': password}
    try:
        r = requests.post(KEYCLOAK_TOKEN_URL, data=payload, timeout=10)
        if r.status_code == 200:
            token = r.json().get("access_token")
            # ვიგებთ ექიმის სახელს
            doc_name = get_name_from_token(token)
            
            resp = RedirectResponse(url="/doctor/worklist", status_code=303)
            resp.set_cookie(key="doctor_token", value=token, httponly=True, max_age=28800)
            # ვინახავთ ექიმის სახელსაც ქუქიში
            resp.set_cookie(key="doc_full_name", value=doc_name, max_age=28800)
            return resp
        return RedirectResponse(url="/login?error=მონაცემები არასწორია", status_code=303)
    except: return RedirectResponse(url="/login?error=კავშირის შეცდომა", status_code=303)
# გამოსვლის ფუნქცია
@app.get("/auth/logout")
async def logout():
    # სისტემიდან გამოსვლა და ქუქიების წაშლა
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
    fname:str="", lname:str="", pid:str="", mod:str="", 
    d_from:str="", d_to:str="", page: int = 1, 
    doctor_token:str=Cookie(None),
    doc_full_name:str=Cookie('Doctor')
):
    if not doctor_token: return RedirectResponse(url="/login")
    
    # 1. ვიღებთ მონაცემებს
    all_studies = get_pacs_studies(fname, lname, pid, mod, d_from, d_to)
    
    # 2. პაგინაციის გამოთვლა
    per_page = 50
    total_count = len(all_studies)
    total_pages = (total_count // per_page) + (1 if total_count % per_page > 0 else 0)
    if total_pages == 0: total_pages = 1
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    studies = all_studies[start_idx:end_idx]

    # 3. ისრების ლინკები
    def get_url(p):
        params = dict(request.query_params)
        params["page"] = p
        return "/doctor/worklist?" + "&".join([f"{k}={v}" for k, v in params.items()])

    # 4. ცხრილის აწყობა
    rows = ""
    for s in studies:
        uid = s.get("0020000D", {}).get("Value", [""])[0]
        raw_name = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "---")
        p_name = raw_name.replace("^", " ")
        p_id = s.get("00100020", {}).get("Value", ["---"])[0]
        p_dob = s.get("00100030", {}).get("Value", ["---"])[0]
        s_date = s.get("00080020", {}).get("Value", ["---"])[0]
        mod_list = s.get("00080061", {}).get("Value", s.get("00080060", {}).get("Value", ["---"]))
        s_mod = ", ".join(mod_list)
        
        f_date = f"{s_date[:4]}-{s_date[4:6]}-{s_date[6:8]}" if len(s_date)==8 else s_date
        f_dob = f"{p_dob[:4]}-{p_dob[4:6]}-{p_dob[6:8]}" if len(p_dob)==8 else p_dob
        safe_name = p_name.replace("'", "\\'")

        rows += f"""<tr>
            <td class="patient-id">{p_id}</td><td class="patient-name">{p_name}</td><td>{f_dob}</td>
            <td><span class="badge-mod">{s_mod}</span></td><td style="font-weight:bold; color:var(--dark);">{f_date}</td>
            <td style="text-align:right;">
                <div style="display:flex; gap:8px; justify-content:flex-end;">
                    <a href="/viewer?StudyInstanceUIDs={uid}" target="_blank" class="btn btn-green">View</a>
                    <a href="/p/download-zip/{uid}/auto" class="btn btn-outline">ZIP</a>
                    <button onclick="openShare('{uid}', '{safe_name}', '{p_id}', '{f_dob}', '{f_date}', '{s_mod}')" class="btn btn-share">Share</button>
                </div>
            </td>
        </tr>"""

    # პაგინაციის ვიზუალი
    pagination = f"""
    <div style="display:flex; justify-content:center; align-items:center; gap:25px; margin:40px 0; padding-bottom:50px;">
        <a href="{get_url(page-1) if page > 1 else '#'}" class="btn btn-dark" style="{'' if page > 1 else 'opacity:0.2; pointer-events:none'}">⬅️ Previous</a>
        <span style="font-weight:900; color:var(--dark);">Page {page} of {total_pages}</span>
        <a href="{get_url(page+1) if page < total_pages else '#'}" class="btn btn-dark" style="{'' if page < total_pages else 'opacity:0.2; pointer-events:none'}">Next ➡️</a>
    </div>
    """

    html = f"""<html><head>{STYLE}<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script><title>Worklist</title></head>
    <body>
        <header>
            <a href="/doctor/worklist" class="h-title">INNOVA <span>PACS</span></a>
            <div style="display:flex; gap:20px; align-items:center;">
                //<a href="/manage/dashboard" style="font-size:10px; font-weight:800; color:#94a3b8; text-decoration:none;">⚙️ ADMIN HUB</a>
                <span style="font-size:12px; color:#94a3b8; font-weight:bold; text-transform:uppercase;">
                    მოგესალმებით, <b style="color:#003366;">{doc_full_name}</b>
                </span>
                <a href="/auth/logout" class="btn btn-dark">LOGOUT</a>
            </div>
        </header>

        <form class="search-panel">
            <a href="/doctor/worklist" style="position:absolute; top:-10px; right:-10px; background:#ef4444; color:white; width:28px; height:28px; border-radius:50%; text-align:center; line-height:28px; text-decoration:none; font-weight:bold; box-shadow:0 4px 10px rgba(239,68,68,0.3);">✕</a>
            <div><label>First Name</label><input type="text" name="fname" value="{fname}" class="in-input"></div>
            <div><label>Last Name</label><input type="text" name="lname" value="{lname}" class="in-input"></div>
            <div><label>National ID</label><input type="text" name="pid" value="{pid}" class="in-input"></div>
            <div><label>Modality</label><select name="mod" class="in-input"><option value="">ALL</option>
                <option value="CT" {'selected' if mod=='CT' else ''}>CT</option><option value="MR" {'selected' if mod=='MR' else ''}>MR</option>
                <option value="US" {'selected' if mod=='US' else ''}>US</option><option value="CR" {'selected' if mod=='CR' else ''}>CR/DX</option>
                <option value="RF" {'selected' if mod=='RF' else ''}>RF</option></select></div>
            <div><label>From</label><input type="date" name="d_from" value="{d_from}" class="in-input"></div>
            <div><label>To</label><input type="date" name="d_to" value="{d_to}" class="in-input"></div>
            <button type="submit" class="btn btn-green shadow-lg" style="height:42px;">SEARCH</button>
        </form>

        <div class="table-container"><table>
            <thead><tr><th>ID</th><th>Patient Name</th><th>Birth Date</th><th>Modality</th><th>Study Date</th><th>Actions</th></tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="6" style="padding:100px; text-align:center; color:#94a3b8; font-style:italic">No records found</td></tr>'}</tbody>
        </table></div>

        {pagination}

        <div id="shareModal" class="modal"><div class="modal-content">
            <h2 id="modalName" style="color:var(--dark); font-weight:900; margin-bottom:25px;">Share Study</h2>
            <div style="background:#f8fafc; padding:20px; border-radius:25px; margin-bottom:20px; border:1px solid #eee;">
                <label style="text-align:left; color:#64748b;">SEND VIA EMAIL</label>
                <input type="email" id="shareEmail" placeholder="Patient Email" class="in-input" style="border:2px solid #e2e8f0; border-radius:15px; padding:12px; margin:10px 0;">
                <button onclick="sendEmail(this)" class="btn btn-green" style="width:100%; padding:12px;">Send Email</button>
            </div>
            <p style="color:#cbd5e1; font-weight:bold; font-size:10px;">OR</p>
            <div style="margin-top:20px;"><button onclick="printQR()" class="btn btn-dark" style="width:100%; padding:15px; background:#334155;">🖨️ Print QR Instructions</button></div>
            <button onclick="document.getElementById('shareModal').style.display='none'" style="margin-top:30px; background:none; border:none; color:#94a3b8; font-weight:800; cursor:pointer; text-transform:uppercase; font-size:10px;">Close Window</button>
        </div></div>

        <script>
    // ლოკალური JS ობიექტი - სჭირდება ორმაგი ფრჩხილი
    let shareData = {{}}; 
    
    // აქ ერთი ფრჩხილია, რადგან პითონმა უნდა ჩასვას ექიმის სახელი
    const docName = "{doc_full_name}"; 

    function openShare(uid, name, pid, dob, s_date, mod) {{
        // მნიშვნელობის მინიჭება - ორმაგი ფრჩხილი
        shareData = {{ uid: uid, name: name, pid: pid, dob: dob, s_date: s_date, mod: mod }};
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
        
        // აქ ერთი ფრჩხილია DOMAIN_NAME-სთვის, დანარჩენი ტექსტია
        fd.append('body', 'https://{DOMAIN_NAME}/p/' + shareData.uid);

        try {{ 
            const res = await fetch('/p/send-email', {{ method: 'POST', body: fd }}); 
            if (res.ok) {{
                alert('მეილი წარმატებით გაიგზავნა!'); 
                document.getElementById('shareModal').style.display = 'none';
            }} else {{
                alert('შეცდომა გაგზავნისას');
            }}
        }} catch (e) {{ 
            alert('კავშირის შეცდომა'); 
        }} finally {{ 
            btn.innerText = 'Send Email'; 
            btn.disabled = false; 
        }}
    }}

    function printQR() {{
        const w = window.open('', '_blank'); 
        const url = "https://{DOMAIN_NAME}/p/" + shareData.uid;
        
        // აქ ვიყენებთ ჩვეულებრივ ბრჭყალებს სტრიქონების გასაერთიანებლად, რომ ფრჩხილებს არ ვებრძოლოთ
        const qrHtml = '<html><head><script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></' + 'script><style>body{{font-family:sans-serif; text-align:center; padding:40px;}}.card{{border:2px dashed #b1d431; padding:30px; border-radius:20px; max-width:400px; margin:auto;}}#q{{display:flex; justify-content:center; margin:20px 0;}}</style></head><body><div class="card"><h2>Innova Medical Center</h2><p>Patient: <b>' + shareData.name + '</b><br>ID: ' + shareData.pid + '</p><div id="q"></div><p>დაასკანერეთ კვლევის სანახავად</p></div><script>new QRCode(document.getElementById("q"), {{text: "' + url + '", width:180, height:180}}); setTimeout(()=>window.print(), 800);</' + 'script></body></html>';
        
        w.document.write(qrHtml);
        w.document.close();
    }}
</script>
    </body></html>"""
    
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, must-revalidate"})
# ==========================================================
# 6. ფაილების მართვა (ZIP გადმოწერა)
# ==========================================================

@app.get("/p/download-zip/{uid}/auto") # ⚡ აქ უნდა იყოს ერთმაგი ფრჩხილი {uid}
async def download_zip(uid: str):
    # 1. საწყისი სახელი (Fallback)
    final_name = f"Innova_Study_{uid[:8]}.zip"
    
    try:
        # 2. ვიღებთ მონაცემებს PACS-იდან სახელის ასაწყობად
        headers = {'Accept': 'application/json'}
        m_res = requests.get(f"{PACS_URL}/studies?StudyInstanceUID={uid}", headers=headers, timeout=5)
        
        if m_res.status_code == 200 and m_res.json():
            m = m_res.json()[0]
            
            # თარიღი (0008,0020)
            date = m.get("00080020", {}).get("Value", ["00000000"])[0]
            
            # პირადი ნომერი (0010,0020)
            pid = m.get("00100020", {}).get("Value", ["000"])[0]
            
            # მოდალობა (0008,0061 ან 0008,0060)
            mod_list = m.get("00080061", {}).get("Value", m.get("00080060", {}).get("Value", ["STUDY"]))
            mod = str(mod_list[0]).replace("/", "-")
            
            # სახელი და გვარი (ინიციალი_გვარი)
            raw_name = m.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "P^Name")
            if "^" in raw_name:
                parts = raw_name.split("^")
                surname = parts[0].strip()
                first_name = parts[1].strip() if len(parts) > 1 else ""
                initial = first_name[0].upper() if first_name else "P"
                name_part = f"{initial}_{surname}"
            else:
                name_part = raw_name.replace(" ", "_")

            # საბოლოო სახელი: YYYYMMDD_Initial_Surname_Mod_PID.zip
            final_name = f"{date}_{name_part}_{mod}_{pid}.zip"
            
    except Exception as e:
        print(f"⚠️ Filename error: {e}")

    # 3. ფაილის გაგზავნა PACS-იდან
    pacs_url = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs/studies/{uid}?accept=application/zip&dicomdir=true"
    
    def iterfile():
        # timeout=None რადგან დიდი არქივების მომზადებას PACS დროს ანდომებს
        with requests.get(pacs_url, stream=True, timeout=None) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=1024*1024):
                yield chunk

    return StreamingResponse(
        iterfile(), 
        media_type="application/zip", 
        headers={"Content-Disposition": f'attachment; filename="{final_name}"'}
    )

# ==========================================================
# 7. კვლევის გაგზავნა ელ ფოსტაზე 
# ==========================================================
@app.post("/p/send-email")
async def send_email(
    email: str = Form(...), 
    body: str = Form(...),
    p_name: str = Form("Unknown"),
    p_id: str = Form("---"), 
    study_date: str = Form("---"), 
    modality: str = Form("---"),
    sender_name: str = Form("Innova PACS")
):
    try:
        # 1. მონაცემების გასუფთავება
        email_final = str(email[0] if isinstance(email, (list, tuple)) else email).strip()
        patient_final = str(p_name[0] if isinstance(p_name, (list, tuple)) else p_name).strip()
        body_final = str(body[0] if isinstance(body, (list, tuple)) else body).strip()
        sender_final = str(sender_name[0] if isinstance(sender_name, (list, tuple)) else sender_name).strip()
        date_final = str(study_date[0] if isinstance(study_date, (list, tuple)) else study_date).strip()
        mod_final = str(modality[0] if isinstance(modality, (list, tuple)) else modality).strip()
        p_id_final = str(p_id[0] if isinstance(p_id, (list, tuple)) else p_id).strip()

        # 2. მეილის ობიექტის აწყობა
        msg = MIMEMultipart('alternative')
        msg['From'] = f"Innova Medical Center <pacs@innovamedical.ge>"
        msg['To'] = email_final
        msg['Subject'] = "თქვენი სამედიცინო კვლევის პასუხი"
        
        html_content = f"""
        <html><body style="font-family: sans-serif; color: #333;">
            <div style="max-width: 600px; margin: auto; border: 1px solid #eee; padding: 30px; border-radius: 15px;">
                <h2 style="color: #004a99; text-align: center;">Innova Medical Center</h2>
                <p>მოგესალმებით, თქვენი კვლევის შედეგები ხელმისაწვდომია პორტალზე.</p>
                <div style="background: #f8f9fa; padding: 15px; border-radius: 10px; margin: 20px 0;">
                    <p>პაციენტი: <b>{patient_final}</b><br>პირადი ნომერი: <b>{p_id_final}</b></p>
                </div>
                <div style="text-align: center;"><a href="{body_final}" target="_blank" style="background: #004a99; color: white; padding: 15px 25px; text-decoration: none; border-radius: 8px; font-weight: bold;">🔍 კვლევის ნახვა</a></div>
            </div>
        </body></html>
        """
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        # 3. SMTP გაგზავნა
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail("pacs@innovamedical.ge", email_final, msg.as_string())
        server.quit()
        
        # 4. ლოგირება (⚡ გადავცემთ ზუსტად 6 მონაცემს!)
        log_share_event(sender_final, email_final, patient_final, p_id_final, date_final, mod_final)
        
        print(f"✅ წარმატებით გაეგზავნა: {patient_final}")
        return {"status": "success"}

    except Exception as e:
        print(f"❌ SMTP Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
# ==========================================================
# 8. პაციენტის პორტალი 
# ==========================================================
#პაციენტის ავტორიზაციის გვერდი
@app.get("/p/{study_uid}", response_class=HTMLResponse)
async def patient_login_page(study_uid: str):
    # პაციენტის საწყისი გვერდი ვერიფიკაციის ფორმით
    html = f"""<html><head>{STYLE}<title>პაციენტის პორტალი</title></head>
    <body class="login-body">
        <form action="/p/verify" method="post" class="login-card">
            <h1 class="h-title">INNOVA <span>PACS</span></h1>
            <p style="font-size:9px; font-weight:800; color:#94a3b8; letter-spacing:2px; margin: 10px 0 30px 0;">PATIENT ACCESS</p>
            
            <input type="hidden" name="uid" value="{study_uid}">
            
            <div style="margin-bottom:15px; text-align:left;">
                <label>პირადი ნომერი</label>
                <input type="text" name="pid" placeholder="11 ნიშნა კოდი" class="in-input" required>
            </div>
            
            <div style="margin-bottom:30px; text-align:left;">
                <label>დაბადების თარიღი</label>
                <input type="text" name="dob" placeholder="მაგ: წელი/თვე/რიცხვი" class="in-input" required>
            </div>
            
            <button type="submit" class="btn btn-green w-full py-4 text-lg shadow-xl">კვლევის ნახვა</button>
            <p style="font-size:10px; color:#999; mt:20px; font-style:italic;">გთხოვთ შეიყვანოთ მონაცემები კვლევის გასახსნელად</p>
        </form>
    </body></html>"""
    return HTMLResponse(content=html)

#პაციენტის ვერიფიკაციის მოდული
@app.post("/p/verify")
async def verify_patient(uid: str = Form(...), pid: str = Form(...), dob: str = Form(...)):
    try:
        # PACS-იდან რეალური მონაცემების გამოთხოვა
        url = f"{PACS_URL}/studies"
        params = {'StudyInstanceUID': uid}
        headers = {'Accept': 'application/json'}
        r = requests.get(url, params=params, headers=headers, timeout=10)

        if r.status_code == 200 and r.json():
            data = r.json()[0]
            real_pid = str(data.get("00100020", {}).get("Value", [""])[0]).strip()
            real_dob = str(data.get("00100030", {}).get("Value", [""])[0]).strip()
            
            # მომხმარებლის შეყვანილი მონაცემების "გასუფთავება"
            u_pid = "".join(filter(str.isdigit, pid))
            u_dob = "".join(filter(str.isdigit, dob))
            
            if u_pid == real_pid and u_dob == real_dob:
                # წარმატება: გადამისამართება Viewer-ზე და Cookie-ს დადება
                resp = RedirectResponse(url=f"/viewer?StudyInstanceUIDs={uid}", status_code=303)
                resp.set_cookie(key="patient_auth", value=uid, max_age=3600, path="/", domain=DOMAIN_NAME, secure=True)
                return resp
        
        return HTMLResponse("<script>alert('მონაცემები არასწორია'); window.history.back();</script>")
    except Exception as e:
        return HTMLResponse(f"ვერიფიკაციის შეცდომა: {str(e)}")