import os, requests, smtplib, json, csv, datetime, base64, zipfile, io, struct, secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, Form, Request, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from langs import T  # ← ლოკალიზაცია ცალკე ფაილიდან

requests.packages.urllib3.disable_warnings()

app = FastAPI()

# One-time download tokens: {token: (uid, expires)}
_usb_tokens: dict = {}

# ==========================================================
# 1. გლობალური კონფიგურაცია
# ==========================================================
CLINIC_NAME   = "Innova Medical Center"
DOMAIN_NAME   = os.getenv("DOMAIN_NAME",   "ris.innovamedical.ge")
AE_TITLE      = os.getenv("AE_TITLE",      "RISINNOVA")
INNOVA_GREEN  = "#b1d431"
DARK_BLUE     = "#003366"

PACS_URL           = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
KEYCLOAK_TOKEN_URL = "http://keycloak:8080/realms/dcm4che/protocol/openid-connect/token"
KEYCLOAK_JWKS_URL  = "http://keycloak:8080/realms/dcm4che/protocol/openid-connect/certs"   # ← ახალი ხაზი
CLIENT_ID          = "risinnova-ui"

import jwt as _jwt                                   # ← ახალი ბლოკის დასაწყისი
from jwt import PyJWKClient as _PyJWKClient

_jwks_client = None

def verify_doctor_token(token: str):
    """
    ვამოწმებთ doctor_token-ის ხელმოწერას Keycloak-ის public key-ით (JWKS) და ვადას.
    აბრუნებს decoded claims dict-ს თუ ვალიდურია, წინააღმდეგ შემთხვევაში None.
    """
    global _jwks_client
    if not token:
        return None
    try:
        if _jwks_client is None:
            _jwks_client = _PyJWKClient(KEYCLOAK_JWKS_URL)
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = _jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return claims
    except Exception as e:
        print(f"doctor_token verification failed: {e}")
        return None
                                                        # ← ახალი ბლოკის დასასრული
OLD_PACS_RS       = os.getenv("OLD_PACS_URL")
OLD_TOKEN_URL     = os.getenv("OLD_KEYCLOAK_URL")
OLD_CLIENT_ID     = os.getenv("OLD_CLIENT_ID")
OLD_CLIENT_SECRET = os.getenv("OLD_CLIENT_SECRET")

DB_NAME = os.getenv("DB_NAME", "pacsdb")               # ← ახალი 3 ხაზი
DB_USER = os.getenv("DB_USER", "pacs")
DB_PASS = os.getenv("DB_PASS")  # საიდუმლო — მხოლოდ .env/environment-იდან, არასდროს hardcode

PROGRESS_FILE    = "/app/migrated_uids.txt"
STATUS_FILE      = "/app/migration_status.txt"
ACTIVE_JSON      = "/app/active_transfers.json"
LIVE_STATUS_FILE = "/app/live_sync_status.txt"
LIVE_ACTIVE_JSON = "/app/live_sync_active.json"
SHARE_LOG_FILE   = "/app/share_logs.csv"

# ==========================================================
# devices.json   — მოწყობილობები / AE Titles
# procedures.json — პროცედურების სია
# (რედაქტირება პირდაპირ ამ ფაილებში, restart არ სჭირდება)
# ==========================================================
_DEVICES_PATH    = os.path.join(os.path.dirname(__file__), "devices.json")
_PROCEDURES_PATH = os.path.join(os.path.dirname(__file__), "procedures.json")

def _load_devices():
    try:
        with open(_DEVICES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("ae_titles", [])
    except Exception as e:
        print(f"⚠️ devices.json შეცდომა: {e}")
        return []

def _load_procedures():
    try:
        with open(_PROCEDURES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("procedures", [])
    except Exception as e:
        print(f"⚠️ procedures.json შეცდომა: {e}")
        return []

SMTP_SERVER = os.getenv("SMTP_SERVER", "relay.mailbaby.net")
SMTP_PORT   = int(os.getenv("SMTP_PORT", 2525))
SMTP_USER   = os.getenv("SMTP_USER",   "mb46636")
SMTP_PASS   = os.getenv("SMTP_PASS",   "Gv2HmQajcsEkAE2j4nUP")

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

    /* ✨ ენის გადამრთველი */
    .lang-switcher {{ display: flex; gap: 4px; background: #f1f5f9; padding: 4px; border-radius: 20px; }}
    .lang-btn {{ padding: 5px 14px; border-radius: 16px; font-size: 11px; font-weight: 800; text-decoration: none; color: #64748b; transition: all 0.2s; letter-spacing: 0.5px; }}
    .lang-btn.active {{ background: var(--dark); color: white; box-shadow: 0 2px 8px rgba(0,51,102,0.25); }}
    .lang-btn:hover:not(.active) {{ background: #e2e8f0; color: var(--dark); }}

    /* სწრაფი ფილტრების ზოლი */
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

    /* ✨ Register MWL Modal */
    .reg-modal-content {{ background: white; margin: 3% auto; padding: 2.5rem 3rem; border-radius: 40px; width: 620px; max-width: 95vw; text-align: left; border-top: 8px solid var(--green); position: relative; box-shadow: 0 30px 60px rgba(0,0,0,0.35); max-height: 92vh; overflow-y: auto; }}
    .reg-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }}
    .reg-field {{ display: flex; flex-direction: column; gap: 5px; }}
    .reg-field.full {{ grid-column: 1 / -1; }}
    .reg-label {{ font-size: 9px; font-weight: 900; color: #94a3b8; text-transform: uppercase; letter-spacing: 1.5px; }}
    .reg-input {{ border: none; border-bottom: 2px solid #e2e8f0; padding: 10px 4px; font-size: 13px; font-weight: 700; color: var(--dark); outline: none; transition: 0.2s; background: transparent; width: 100%; box-sizing: border-box; }}
    .reg-input:focus {{ border-bottom-color: var(--green); }}
    .reg-input[readonly] {{ color: #94a3b8; cursor: default; }}
    .reg-section {{ font-size: 9px; font-weight: 900; color: var(--green); text-transform: uppercase; letter-spacing: 2px; margin: 24px 0 4px 0; padding-bottom: 6px; border-bottom: 1px solid #f1f5f9; grid-column: 1 / -1; }}
    .btn-reg {{ background: var(--green); color: white; border: none; padding: 14px 30px; border-radius: 16px; font-weight: 900; font-size: 13px; cursor: pointer; width: 100%; margin-top: 24px; letter-spacing: 0.5px; transition: 0.2s; }}
    .btn-reg:hover {{ opacity: 0.88; }}
    .btn-reg:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    .reg-badge {{ display: inline-flex; align-items: center; gap: 6px; background: #f0fdf4; color: #166534; padding: 6px 14px; border-radius: 20px; font-size: 11px; font-weight: 800; margin-bottom: 16px; }}
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
                writer.writerow(["გაგზავნის დრო", "გამგზავნი ექიმი", "ადრესატი (Email)",
                                  "პაციენტი", "პირადი ნომერი", "კვლევის თარიღი", "Modality"])
            writer.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             str(sender), str(recipient), str(p_name),
                             str(p_id), str(study_date), str(modality)])
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
        data = {"client_id": OLD_CLIENT_ID, "client_secret": OLD_CLIENT_SECRET,
                "grant_type": "client_credentials"}
        r = requests.post(OLD_TOKEN_URL, data=data, timeout=10, verify=False)
        return r.json().get("access_token")
    except:
        return None

def get_pacs_studies(fname="", lname="", pid="", mod="", d_from="", d_to=""):
    ALLOWED_MODALITIES = ["CT", "MR", "US", "RF", "CR", "ES", "XC", "DX", "XA"]
    try:
        params = {'includefield': 'all', 'limit': 1000, 'fuzzymatching': 'false'}
        f_search = fname.upper().strip()
        l_search = lname.upper().strip()
        # PACS-ის query-ს განზრახ ვაძლევთ მხოლოდ ერთ სიტყვას (ფართო filter),
        # რომ თანმიმდევრობაზე (გვარი-სახელი vs სახელი-გვარი) დამოკიდებული არ იყოს —
        # ორივე სიტყვის ზუსტი დამთხვევა ქვემოთ, ლოკალურ filter-ში მოწმდება, თანმიმდევრობის მიუხედავად
        if l_search:   params['PatientName'] = f"*{l_search}*"
        elif f_search: params['PatientName'] = f"*{f_search}*"
        if pid: params['PatientID'] = pid
        if d_from or d_to:
            start = d_from.replace("-", "") if d_from else "20100101"
            end   = d_to.replace("-", "")   if d_to   else datetime.datetime.now().strftime("%Y%m%d")
            params['StudyDate'] = f"{start}-{end}"
        else:
            params['StudyDate'] = "20200101-"
        r = requests.get(PACS_URL + "/studies", params=params,
                         headers={'Accept': 'application/json'}, timeout=15)
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

        def _tag_val(item, tag, default):
            values = item.get(tag, {}).get("Value") or [default]
            return values[0] if values else default

        strict_results.sort(key=lambda x: (
            _tag_val(x, "00080020", "00000000"),
            _tag_val(x, "00080030", "000000")
        ), reverse=True)
        return strict_results
    except:
        return []

# ==========================================================
# 4. ავტორიზაცია
# ==========================================================
@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = None, lang: str = "ka"):
    err = f'<div style="color:#ef4444;font-weight:bold;font-size:12px;margin-bottom:20px;">{error}</div>' if error else ""
    return f"""<html><head>{STYLE}<title>{T(lang,'app_title')}</title></head>
    <body class="login-body">
        <form action="/auth/login" method="post" class="login-card">
            <h1 class="h-title">INNOVA <span>PACS</span></h1>
            <p style="font-size:9px;font-weight:800;color:#94a3b8;letter-spacing:2px;margin:10px 0 40px 0;">{T(lang,'login_subtitle')}</p>
            {err}
            <input type="hidden" name="lang" value="{lang}">
            <div style="margin-bottom:20px"><input type="text" name="username" placeholder="{T(lang,'username')}" class="in-input" required></div>
            <div style="margin-bottom:40px"><input type="password" name="password" placeholder="{T(lang,'password')}" class="in-input" required></div>
            <button type="submit" class="btn btn-green" style="width:100%;padding:14px;font-size:13px;">{T(lang,'login_btn')}</button>
            <div style="margin-top:20px;display:flex;justify-content:center;gap:8px;background:#f1f5f9;padding:6px;border-radius:20px;width:fit-content;margin-left:auto;margin-right:auto;">
                <a href="/login?lang=ka" style="padding:5px 14px;border-radius:16px;font-size:11px;font-weight:800;text-decoration:none;{'background:var(--dark);color:white;' if lang=='ka' else 'color:#64748b;'}">KA</a>
                <a href="/login?lang=en" style="padding:5px 14px;border-radius:16px;font-size:11px;font-weight:800;text-decoration:none;{'background:var(--dark);color:white;' if lang=='en' else 'color:#64748b;'}">EN</a>
            </div>
        </form>
    </body></html>"""

@app.post("/auth/login")
async def auth_login(username: str = Form(...), password: str = Form(...), lang: str = Form("ka")):
    payload = {'grant_type': 'password', 'client_id': CLIENT_ID,
               'username': username, 'password': password}
    try:
        r = requests.post(KEYCLOAK_TOKEN_URL, data=payload, timeout=10)
        if r.status_code == 200:
            token    = r.json().get("access_token")
            doc_name = get_name_from_token(token)
            resp = RedirectResponse(url=f"/doctor/worklist?lang={lang}", status_code=303)
            resp.set_cookie(key="doctor_token",  value=token,    httponly=True, max_age=28800)
            resp.set_cookie(key="doc_full_name", value=doc_name, max_age=28800)
            resp.set_cookie(key="ui_lang",       value=lang,     max_age=28800 * 30)
            return resp
        return RedirectResponse(url=f"/login?lang={lang}&error={T(lang,'login_err')}", status_code=303)
    except:
        return RedirectResponse(url=f"/login?lang={lang}&error={T(lang,'conn_err')}", status_code=303)

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
    request:       Request,
    pname:         str = "", pid: str = "", mod: str = "",
    d_from:        str = "", d_to:   str = "", page: int = 1,
    quick:         str = "",
    lang:          str = "ka",
    doctor_token:  str = Cookie(None),
    doc_full_name: str = Cookie("Doctor"),
    ui_lang:       str = Cookie("ka"),
):
    if not verify_doctor_token(doctor_token):
        return RedirectResponse(url="/login")

    # query param > cookie
    if not lang:
        lang = ui_lang or "ka"
    # ერთი გაერთიანებული "სახელი გვარი" ველიდან fname/lname-ის გამოყოფა
    # (get_pacs_studies() უცვლელად ელოდება ცალკე fname/lname-ს)
    name_parts = pname.strip().split(None, 1)
    if len(name_parts) == 2:
        lname, fname = name_parts[0], name_parts[1]
    elif len(name_parts) == 1:
        lname, fname = name_parts[0], ""
    else:
        lname = fname = ""

    # სწრაფი ფილტრების თარიღები
    today = datetime.date.today()
    if quick == "today":
        d_from = d_to = today.strftime("%Y-%m-%d")
    elif quick == "yesterday":
        yest   = today - datetime.timedelta(days=1)
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
        params["lang"] = lang
        return "/doctor/worklist?" + "&".join(f"{k}={v}" for k, v in params.items())

    def qf_url(q):
        parts = [f"lang={lang}"]
        if pname: parts.append(f"pname={pname}")
        if pid:   parts.append(f"pid={pid}")
        if mod:   parts.append(f"mod={mod}")
        parts.append(f"quick={q}")
        return "/doctor/worklist?" + "&".join(parts)

    def lang_url(l):
        params = dict(request.query_params)
        params["lang"] = l
        params["page"] = 1
        return "/doctor/worklist?" + "&".join(f"{k}={v}" for k, v in params.items())

    # ✨ ენის გადამრთველი ვიჯეტი
    lang_switcher = f"""
    <div class="lang-switcher">
        <a href="{lang_url('ka')}" class="lang-btn {'active' if lang == 'ka' else ''}">KA</a>
        <a href="{lang_url('en')}" class="lang-btn {'active' if lang == 'en' else ''}">EN</a>
    </div>"""

    # ✨ InstitutionName — batch parallel fetch (series-level, ყველა study ერთდროულად)
    def _fetch_institution(study_uid):
        try:
            r = requests.get(
                f"{PACS_URL}/studies/{study_uid}/series",
                params={"includefield": "00080080", "limit": 1},
                headers={"Accept": "application/json"}, timeout=4
            )
            if r.status_code == 200 and r.json():
                val = ((r.json()[0].get("00080080", {}).get("Value") or [""])[0] or "").strip()
                return study_uid, val
        except:
            pass
        return study_uid, ""

    # მხოლოდ იმ study-ებს ვთხოვთ, სადაც study-level institution ცარიელია
    institution_map = {}
    needs_fetch = []
    for s in studies:
        u = s.get("0020000D", {}).get("Value", [""])[0]
        val = ((s.get("00080080", {}).get("Value") or [""])[0] or "").strip()
        if val:
            institution_map[u] = val
        else:
            needs_fetch.append(u)

    if needs_fetch:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_fetch_institution, u): u for u in needs_fetch}
            for f in as_completed(futures):
                uid_r, val = f.result()
                institution_map[uid_r] = val or CLINIC_NAME

    # ცხრილის სტრიქონები
    rows = ""
    for s in studies:
        uid      = s.get("0020000D", {}).get("Value", [""])[0]
        raw_name = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "---")
        p_name   = raw_name.replace("^", " ")
        p_id     = s.get("00100020", {}).get("Value", ["---"])[0]
        p_dob    = s.get("00100030", {}).get("Value", ["---"])[0]
        s_date   = s.get("00080020", {}).get("Value", ["---"])[0]
        s_time   = s.get("00080030", {}).get("Value", [""])[0]  # StudyTime (0008,0030)
        mod_list = s.get("00080061", {}).get("Value", s.get("00080060", {}).get("Value", ["---"]))
        s_mod    = ", ".join(mod_list)

        # InstitutionName — DICOM study-level-ზე არ ბრუნდება (series attribute),
        # ამიტომ პირდაპირ CLINIC_NAME კონსტანტას ვიყენებთ (ყოველგვარი დამატებითი call-ის გარეშე)
        hospital = institution_map.get(uid, CLINIC_NAME)
        study_desc = ((s.get("00081030", {}).get("Value") or [""])[0] or "").strip() or "—"
        num_images = str((s.get("00201208", {}).get("Value") or ["—"])[0])
        num_series = str((s.get("00201206", {}).get("Value") or ["—"])[0])

        f_date    = f"{s_date[6:8]}.{s_date[4:6]}.{s_date[:4]}" if len(s_date) == 8 else s_date
        s_time    = str(s_time).split(".")[0]  # წამის ნაწილი მოვაცილოთ
        f_time    = f"{s_time[:2]}:{s_time[2:4]}" if len(s_time) >= 4 else ""
        f_dob     = f"{p_dob[6:8]}.{p_dob[4:6]}.{p_dob[:4]}"   if len(p_dob)  == 8 else p_dob
        safe_name = p_name.replace("'", "\\'")
        hosp_cls  = "" if hospital   != "—" else " empty"
        desc_cls  = "" if study_desc != "—" else " empty"

        rows += f"""<tr>
            <td style="font-size:11px;color:#64748b;">{p_id}</td>
            <td style="font-weight:700;color:var(--dark);">{p_name}</td>
            <td style="font-size:11px;">{f_dob}</td>
            <td><span class="badge-mod">{s_mod}</span></td>
            <td style="font-weight:800;color:var(--dark);font-size:12px;">
                {f_date}
                {f'<span style="font-size:10px;color:#94a3b8;font-weight:600;margin-left:5px;">⏱ ' + f_time + '</span>' if f_time else ""}
            </td>
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
                    <button onclick="openInDiagnosticMonitor('{uid}')" class="btn btn-green">View</button>
                    <a href="/p/download-zip/{uid}/auto" class="btn btn-outline">ZIP</a>
                    <button onclick="openShare('{uid}','{safe_name}','{p_id}','{f_dob}','{f_date}','{s_mod}')" class="btn btn-share">Share</button>
                    <button onclick="openRegister('{safe_name}','{p_id}','{f_dob}','{s_mod}')" class="btn" style="background:#7c3aed;color:white;">📋 Reg</button>
                    {"<a href='/doctor/dexa-report/" + uid + "?lang=" + lang + "' target='_blank' class='btn' style='background:#0f766e;color:white;'>🦴 DEXA</a>" if "BMD" in s_mod else ""}
                </div>
            </td>
        </tr>"""

    pagination = f"""
    <div style="display:flex;justify-content:center;align-items:center;gap:25px;margin:30px 0 50px 0;">
        <a href="{get_url(page-1)}" class="btn btn-dark" style="{'opacity:0.25;pointer-events:none' if page<=1 else ''}">{T(lang,'prev')}</a>
        <span style="font-weight:900;color:var(--dark);">{T(lang,'page')} {page} {T(lang,'of')} {total_pages}</span>
        <a href="{get_url(page+1)}" class="btn btn-dark" style="{'opacity:0.25;pointer-events:none' if page>=total_pages else ''}">{T(lang,'next')}</a>
    </div>"""

    clear_btn = f'<a href="/doctor/worklist?lang={lang}" class="qf clear">{T(lang,"qf_clear")}</a>' if quick else ""
    quick_bar = f"""
    <div class="quick-bar">
        <span class="quick-bar-label">{T(lang,'qf_label')}</span>
        <a href="{qf_url('today')}"     class="qf {'active' if quick=='today'     else ''}">{T(lang,'qf_today')}</a>
        <a href="{qf_url('yesterday')}" class="qf {'active' if quick=='yesterday' else ''}">{T(lang,'qf_yesterday')}</a>
        <a href="{qf_url('month')}"     class="qf {'active' if quick=='month'     else ''}">{T(lang,'qf_month')}</a>
        <a href="{qf_url('year')}"      class="qf {'active' if quick=='year'      else ''}">{T(lang,'qf_year')}</a>
        {clear_btn}
        <span class="qf-total">{T(lang,'qf_total')}: <b>{total_count}</b> {T(lang,'qf_studies')}</span>
    </div>"""

    # ── Devices dropdown options (devices.json-იდან, reload ყოველ request-ზე)
    ae_options = "\n".join(
        f'<option value="{d["ae"]}" data-mod="{d["modality"]}">{d["label"]}</option>'
        for d in _load_devices()
    )
    proc_options = "\n".join(
        f'<option value="{p["label"]}" data-mod="{p["modality"]}">{p["label"]}</option>'
        for p in _load_procedures()
    )

    # JS alert სტრინგები ენის მიხედვით
    js_ok      = T(lang, 'share_ok')
    js_err     = T(lang, 'share_err')
    js_conn    = T(lang, 'share_conn_err')
    js_invalid = T(lang, 'share_invalid')
    js_sending = T(lang, 'share_sending')
    js_sendbtn = T(lang, 'share_send_btn')

    html = f"""<html><head>{STYLE}
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.js"></script>
    <title>{T(lang,'app_title')}</title></head>
    <body>
        <header>
            <a href="/doctor/worklist?lang={lang}" class="h-title">INNOVA <span>PACS</span></a>
            <div style="display:flex;gap:16px;align-items:center;">
                <span style="font-size:12px;color:#94a3b8;font-weight:bold;text-transform:uppercase;">
                    {T(lang,'welcome')}, <b style="color:#003366;">{doc_full_name}</b>
                </span>
                {lang_switcher}
                <button onclick="openNewPatient()" class="btn" style="background:#7c3aed;color:white;">➕ ახალი პაციენტი</button>
                <button onclick="openUpload()" class="btn" style="background:#0369a1;color:white;">⬆ DICOM ატვირთვა</button>
                <a href="/doctor/stats" target="_blank" class="btn" style="background:#0f766e;color:white;">📊 სტატისტიკა</a>
                <a href="/auth/logout" class="btn btn-dark">{T(lang,'logout')}</a>
            </div>
        </header>

        {quick_bar}

        <form class="search-panel" method="get" action="/doctor/worklist" onsubmit="return validateDateRange()">
            <input type="hidden" name="lang" value="{lang}">
            <a href="/doctor/worklist?lang={lang}" style="position:absolute;top:-10px;right:-10px;background:#ef4444;color:white;width:28px;height:28px;border-radius:50%;text-align:center;line-height:28px;text-decoration:none;font-weight:bold;box-shadow:0 4px 10px rgba(239,68,68,0.3);">✕</a>
            <div><label>{T(lang,'pname')}</label><input type="text" name="pname" value="{pname}" class="in-input" placeholder="{T(lang,'pname_ph')}"></div>
            <div><label>{T(lang,'pid')}</label><input type="text" name="pid" value="{pid}" class="in-input"></div>
            <div><label>{T(lang,'modality')}</label>
                <select name="mod" class="in-input">
                    <option value="">{T(lang,'mod_all')}</option>
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
            <div><label>{T(lang,'date_from')}</label><input type="date" id="d_from_input" name="d_from" value="{d_from}" class="in-input" placeholder="{T(lang,'date_placeholder')}"></div>
            <div><label>{T(lang,'date_to')}</label><input type="date" id="d_to_input" name="d_to" value="{d_to}" class="in-input" placeholder="{T(lang,'date_placeholder')}"></div>
            <button type="submit" class="btn btn-green" style="height:42px;">{T(lang,'search_btn')}</button>
        </form>

        <script>
        flatpickr("#d_from_input", {{ dateFormat: "Y-m-d", altInput: true, altFormat: "d.m.Y", allowInput: true }});
        flatpickr("#d_to_input",   {{ dateFormat: "Y-m-d", altInput: true, altFormat: "d.m.Y", allowInput: true }});

        function validateDateRange() {{
            var f = document.getElementById('d_from_input').value;
            var t = document.getElementById('d_to_input').value;
            if (f && t && f > t) {{
                alert("{T(lang,'date_range_err')}");
                return false;
            }}
            return true;
        }}
        </script>

        <div class="table-wrapper">
            <div class="table-container">
                <table>
                    <thead><tr>
                        <th>{T(lang,'col_id')}</th>
                        <th>{T(lang,'col_patient')}</th>
                        <th>{T(lang,'col_dob')}</th>
                        <th>{T(lang,'col_modality')}</th>
                        <th>{T(lang,'col_study_date')}</th>
                        <th>{T(lang,'col_institution')}</th>
                        <th>{T(lang,'col_description')}</th>
                        <th>{T(lang,'col_images')}</th>
                        <th style="text-align:right;">{T(lang,'col_actions')}</th>
                    </tr></thead>
                    <tbody>
                        {rows if rows else f'<tr><td colspan="9" style="padding:80px;text-align:center;color:#94a3b8;font-style:italic;">{T(lang,"no_records")}</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        {pagination}

        <div id="shareModal" class="modal">
            <div class="modal-content">
                <h2 id="modalName" style="color:var(--dark);font-weight:900;margin-bottom:25px;">{T(lang,'share_title')}</h2>
                <div style="background:#f8fafc;padding:20px;border-radius:25px;margin-bottom:20px;border:1px solid #eee;">
                    <label style="text-align:left;color:#64748b;">{T(lang,'share_email_lbl')}</label>
                    <input type="email" id="shareEmail" placeholder="{T(lang,'share_email_ph')}"
                           class="in-input" style="border:2px solid #e2e8f0;border-radius:15px;padding:12px;margin:10px 0;">
                    <button onclick="sendEmail(this)" class="btn btn-green" style="width:100%;padding:12px;">{T(lang,'share_send_btn')}</button>
                </div>
                <p style="color:#cbd5e1;font-weight:bold;font-size:10px;">{T(lang,'share_or').upper()}</p>
                <div style="margin-top:20px;display:flex;flex-direction:column;gap:10px;">
                    <button onclick="printQR()" class="btn btn-dark" style="width:100%;padding:15px;background:#334155;">{T(lang,'share_qr_btn')}</button>
                    <button id="usb-export-btn" onclick="openUsbWriter()"
                       style="width:100%;padding:15px;background:#0f766e;color:white;border-radius:12px;font-weight:800;
                              font-size:11px;text-transform:uppercase;text-align:center;cursor:pointer;
                              border:none;letter-spacing:0.5px;box-sizing:border-box;">
                       💾 CD/USB-ზე ჩაწერა
                    </button>
                </div>
                <button onclick="document.getElementById('shareModal').style.display='none'"
                    style="margin-top:30px;background:none;border:none;color:#94a3b8;font-weight:800;cursor:pointer;text-transform:uppercase;font-size:10px;">
                    {T(lang,'share_close')}
                </button>
            </div>
        </div>

        <script>
        let shareData  = {{}};
        const docName  = "{doc_full_name}";
        const MSG_OK      = "{js_ok}";
        const MSG_ERR     = "{js_err}";
        const MSG_CONN    = "{js_conn}";
        const MSG_INVALID = "{js_invalid}";
        const MSG_SENDING = "{js_sending}";
        const MSG_SENDBTN = "{js_sendbtn}";

        // ══ DICOM Upload ════════════════════════════════════════
        let upFiles = [];
        let upActiveSource = 'files';

        function openUpload(pid='', name='') {{
            upFiles = [];
            document.getElementById('up-filelist').innerHTML = '';
            document.getElementById('up-result').innerHTML   = '';
            document.getElementById('up-pid').value  = pid;
            document.getElementById('up-src').value  = '';
            document.getElementById('up-note').value = '';
            document.getElementById('up-btn').disabled = false;
            document.getElementById('up-btn').innerText = '⬆ PACS-ში ატვირთვა';
            const lbl = document.getElementById('up-patient-label');
            if (name && pid) {{
                lbl.innerHTML = '<b style="color:#003366;">' + name + '</b> · <span style="color:#0369a1;">' + pid + '</span>';
                document.getElementById('up-pid').style.background = '#f0fdf4';
                document.getElementById('up-pid').style.borderColor = '#86efac';
            }} else {{
                lbl.innerHTML = 'PACS არქივში ატვირთვა · .dcm, ZIP, ფოლდერი · DICOMDIR საჭირო არ არის';
                document.getElementById('up-pid').style.background = '#f8fafc';
                document.getElementById('up-pid').style.borderColor = '#e2e8f0';
            }}
            chooseSource('files');
            document.getElementById('upload-modal').style.display = 'block';
        }}

        function closeUpload() {{
            document.getElementById('upload-modal').style.display = 'none';
        }}

        function chooseSource(type) {{
            upActiveSource = type;
            ['files','zip','folder'].forEach(t => {{
                const btn = document.getElementById('src-' + t);
                if (t === type) {{
                    btn.style.borderColor  = '#0369a1';
                    btn.style.background   = '#eff6ff';
                    btn.style.color        = '#1e40af';
                }} else {{
                    btn.style.borderColor  = '#e2e8f0';
                    btn.style.background   = '#f8fafc';
                    btn.style.color        = '#64748b';
                }}
            }});
        }}

        function triggerActive() {{
            document.getElementById('inp-' + upActiveSource).click();
        }}

        function onDrop(e) {{
            e.preventDefault();
            document.getElementById('up-dropzone').style.borderColor = '#bfdbfe';
            const items = e.dataTransfer.items;
            const files = [];
            if (items) {{
                for (let i = 0; i < items.length; i++) {{
                    if (items[i].kind === 'file') files.push(items[i].getAsFile());
                }}
            }} else {{
                for (let f of e.dataTransfer.files) files.push(f);
            }}
            processFileList(files);
        }}

        function onFilesChosen(fileList, type) {{
            processFileList(Array.from(fileList));
        }}

        function processFileList(files) {{
            // ნებისმიერი ფაილი ვიღებთ — .dcm, .zip, ან ყველა ფოლდერიდან
            upFiles = files.filter(f => {{
                const n = f.name.toLowerCase();
                return n.endsWith('.dcm') || n.endsWith('.zip') ||
                       n.endsWith('.iso') || !n.includes('.');
            }});
            if (!upFiles.length) {{
                // თუ ვერ გაფილტრა — ყველა ვიღებთ (ზოგ CD-ზე extension-ი არ აქვს)
                upFiles = files;
            }}
            renderFileList();
        }}

        function renderFileList() {{
            const el = document.getElementById('up-filelist');
            if (!upFiles.length) {{ el.innerHTML = ''; return; }}
            const dcmCount = upFiles.filter(f => f.name.toLowerCase().endsWith('.dcm')).length;
            const zipCount = upFiles.filter(f => f.name.toLowerCase().endsWith('.zip')).length;
            const otherCount = upFiles.length - dcmCount - zipCount;
            const totalMB = (upFiles.reduce((a,f)=>a+f.size,0)/1024/1024).toFixed(1);
            el.innerHTML = `
              <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;
                          padding:10px 14px;font-size:13px;color:#166534;">
                ✓ <b>${{upFiles.length}}</b> ფაილი შერჩეული
                ${{dcmCount ? ' · <b>' + dcmCount + '</b> .dcm' : ''}}
                ${{zipCount ? ' · <b>' + zipCount + '</b> .zip' : ''}}
                ${{otherCount > 0 ? ' · <b>' + otherCount + '</b> სხვა' : ''}}
                · სულ <b>${{totalMB}} MB</b>
              </div>`;
        }}

        async function submitUpload() {{
            const pid  = document.getElementById('up-pid').value.trim();
            const src  = document.getElementById('up-src').value.trim();
            const note = document.getElementById('up-note').value.trim();
            const btn  = document.getElementById('up-btn');
            const res  = document.getElementById('up-result');

            if (!upFiles.length) {{ res.innerHTML = '<div style="color:#ef4444;font-size:13px;">ფაილი არ არის შერჩეული</div>'; return; }}

            btn.disabled = true;
            btn.innerText = '⏳ იტვირთება...';
            res.innerHTML = '';

            const fd = new FormData();
            upFiles.forEach(f => fd.append('files', f));
            fd.append('source', src);
            fd.append('note',   note);
            if (pid) fd.append('override_pid', pid);

            try {{
                const r    = await fetch('/doctor/upload/submit', {{method:'POST', body:fd, credentials:'include'}});
                const data = await r.json();
                if (data.status === 'ok') {{
                    btn.innerText = '✅ ატვირთვა დასრულდა';
                    btn.style.background = '#10b981';
                    res.innerHTML = `<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;padding:12px 14px;font-size:13px;color:#166534;">
                      ✅ ატვირთულია <b>${{data.total_instances}}</b> instance, <b>${{data.studies}}</b> კვლევა.
                      <a href="/doctor/worklist" style="color:#166534;font-weight:700;margin-left:8px;">Worklist-ში ნახვა →</a>
                    </div>`;
                }} else {{
                    btn.disabled = false;
                    btn.innerText = '⬆ PACS-ში ატვირთვა';
                    res.innerHTML = '<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:10px;padding:12px 14px;font-size:13px;color:#991b1b;">❌ ' + (data.detail||'შეცდომა') + '</div>';
                }}
            }} catch(e) {{
                btn.disabled = false;
                btn.innerText = '⬆ PACS-ში ატვირთვა';
                res.innerHTML = '<div style="color:#ef4444;font-size:13px;">❌ კავშირის შეცდომა: ' + e.message + '</div>';
            }}
        }}

        // close on backdrop click
        window.addEventListener('load', function() {{
            var um = document.getElementById('upload-modal');
            if (um) um.addEventListener('click', function(e) {{
                if (e.target === this) closeUpload();
            }});
        }});
        // ══════════════════════════════════════════════════════

        async function openInDiagnosticMonitor(studyUID) {{
            const viewerUrl = "/viewer?StudyInstanceUIDs=" + studyUID;
            if ('getScreenDetails' in window) {{
                try {{
                    const screenDetails = await window.getScreenDetails();
                    const allScreens    = screenDetails.screens;
                    const diagnosticScreen = allScreens.find(s => !s.isPrimary);
                    if (diagnosticScreen) {{
                        const features = [
                            "left="   + diagnosticScreen.availLeft,
                            "top="    + diagnosticScreen.availTop,
                            "width="  + diagnosticScreen.availWidth,
                            "height=" + diagnosticScreen.availHeight,
                            "menubar=no","toolbar=no","location=no",
                            "status=no","resizable=yes","scrollbars=yes"
                        ].join(",");
                        window.open(viewerUrl, "OHIFViewer", features);
                    }} else {{
                        window.open(viewerUrl, "_blank");
                    }}
                }} catch (err) {{
                    window.open(viewerUrl, "_blank");
                }}
            }} else {{
                window.open(viewerUrl, "_blank");
            }}
        }}

        function openShare(uid, name, pid, dob, s_date, mod) {{
            shareData = {{ uid, name, pid, dob, s_date, mod }};
            document.getElementById('modalName').innerText = name;
            document.getElementById('shareModal').style.display = 'block';
            document.getElementById('shareEmail').value = '';
            // USB export
            // one-time token სერვერიდან
            _currentUsbUid = uid;
            _currentUsbLabel = name + ' | ' + s_date + ' | ' + mod;
        }}

        var _currentUsbUrl   = '';
        var _currentUsbLabel = '';

        var _currentUsbUid   = '';

        async function openUsbWriter() {{
            if (!_currentUsbUid) return;
            try {{
                // სერვერიდან one-time token
                const res = await fetch('/doctor/usb-token/' + _currentUsbUid, {{
                    credentials: 'include'
                }});
                const data = await res.json();
                if (!data.token) {{ alert('ავტორიზაციის შეცდომა'); return; }}

                const zipUrl  = 'https://{DOMAIN_NAME}/doctor/usb-export/' + _currentUsbUid + '?token=' + data.token;
                const exeUrl  = 'innova-usb://' + encodeURIComponent(zipUrl)
                              + '?label=' + encodeURIComponent(_currentUsbLabel);
                window.location.href = exeUrl;
            }} catch(e) {{
                alert('შეცდომა: ' + e.message);
            }}
        }}

        async function sendEmail(btn) {{
            const email = document.getElementById('shareEmail').value;
            if (!email || !email.includes('@')) return alert(MSG_INVALID);
            btn.innerText = MSG_SENDING;
            btn.disabled  = true;
            const fd = new FormData();
            fd.append('email',       email);
            fd.append('p_name',      shareData.name);
            fd.append('p_id',        shareData.pid);
            fd.append('study_date',  shareData.s_date);
            fd.append('modality',    shareData.mod);
            fd.append('sender_name', docName);
            fd.append('body', 'https://{DOMAIN_NAME}/p/' + shareData.uid);
            try {{
                const res = await fetch('/p/send-email', {{ method: 'POST', body: fd }});
                if (res.ok) {{
                    alert(MSG_OK);
                    document.getElementById('shareModal').style.display = 'none';
                }} else alert(MSG_ERR);
            }} catch(e) {{ alert(MSG_CONN); }}
            finally {{ btn.innerText = MSG_SENDBTN; btn.disabled = false; }}
        }}

        function printQR() {{
            const w   = window.open('', '_blank');
            const url = "https://{DOMAIN_NAME}/p/" + shareData.uid;
            const qrHtml = '<html><head><script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></'
                + 'script><style>body{{font-family:sans-serif;text-align:center;padding:40px;}}'
                + '.card{{border:2px dashed #b1d431;padding:30px;border-radius:20px;max-width:400px;margin:auto;}}'
                + '#q{{display:flex;justify-content:center;margin:20px 0;}}</style></head><body>'
                + '<div class="card"><h2>Innova Medical Center</h2>'
                + '<p>Patient: <b>' + shareData.name + '</b><br>ID: ' + shareData.pid + '</p>'
                + '<div id="q"></div><p>დაასკანერეთ კვლევის სანახავად</p></div>'
                + '<script>new QRCode(document.getElementById("q"),{{text:"' + url + '",width:180,height:180}});'
                + 'setTimeout(()=>window.print(),800);</' + 'script></body></html>';
            w.document.write(qrHtml);
            w.document.close();
        }}
        // ══════════════════════════════════════════
        // MWL Registration
        // ══════════════════════════════════════════
        let regData = {{}};

        // მოდალობის მიხედვით AE Title და Procedure ფილტრაცია
        // ══════════════════════════════════════════
        // პაციენტის ძებნა PACS-ში პირადი ნომრით
        // ══════════════════════════════════════════
        async function lookupPatient() {{
            const pid = document.getElementById('lookup_pid').value.trim();
            if (!pid) return;

            const statusEl = document.getElementById('lookup_status');
            statusEl.innerHTML = '<div class="lookup-status" style="background:#f1f5f9;color:#64748b;">⏳ ძებნა...</div>';

            try {{
                const res = await fetch('/doctor/mwl/lookup-patient?pid=' + encodeURIComponent(pid), {{
                    method:      'GET',
                    credentials: 'include',
                    headers:     {{'Accept': 'application/json'}}
                }});

                if (res.status === 401) {{
                    statusEl.innerHTML = '<div class="lookup-status" style="background:#fef2f2;color:#991b1b;">❌ სესია ამოიწურა — გთხოვთ განახლოთ გვერდი</div>';
                    return;
                }}
                if (!res.ok) {{
                    statusEl.innerHTML = '<div class="lookup-status" style="background:#fef2f2;color:#991b1b;">❌ სერვერის შეცდომა (' + res.status + ')</div>';
                    return;
                }}

                const data = await res.json();

                if (data.found) {{
                    // ✅ PACS-ში არსებობს — ავტოშევსება
                    document.getElementById('reg_p_name').value = data.name;
                    document.getElementById('reg_p_id').value   = data.pid;
                    document.getElementById('reg_p_dob').value  = data.dob;
                    document.getElementById('reg_p_sex').value  = data.sex || '';
                    statusEl.innerHTML = '<div class="lookup-status lookup-found">✅ პაციენტი ნაპოვნია — მონაცემები ჩაიწერა</div>';
                }} else {{
                    // ⚠️ ახალი პაციენტი — ხელით შევსება
                    document.getElementById('reg_p_name').value = '';
                    document.getElementById('reg_p_id').value   = pid;
                    document.getElementById('reg_p_dob').value  = '';
                    document.getElementById('reg_p_sex').value  = '';
                    statusEl.innerHTML = '<div class="lookup-status lookup-new">⚠️ პაციენტი ვერ მოიძებნა — შეავსეთ ხელით</div>';
                    document.getElementById('reg_p_name').focus();
                }}
            }} catch(e) {{
                console.error('lookup error:', e);
                statusEl.innerHTML = '<div class="lookup-status" style="background:#fef2f2;color:#991b1b;">❌ კავშირის შეცდომა: ' + e.message + '</div>';
            }}
        }}

        // ახალი პაციენტი — header ღილაკი
        function openNewPatient() {{
            // ველების გასუფთავება
            document.getElementById('lookup_pid').value     = '';
            document.getElementById('lookup_status').innerHTML = '';
            document.getElementById('reg_p_name').value    = '';
            document.getElementById('reg_p_id').value      = '';
            document.getElementById('reg_p_dob').value     = '';
            document.getElementById('reg_p_sex').value     = '';
            document.getElementById('reg_modality').value  = '';
            document.getElementById('reg_procedure').value = '';
            document.getElementById('reg_referring').value = docName;
            document.getElementById('reg_ae_title').value  = '';
            document.getElementById('reg_sched_dt').value  = new Date().toISOString().slice(0,16);
            document.getElementById('reg_result').innerHTML = '';
            document.getElementById('reg_submit').disabled  = false;
            document.getElementById('reg_submit').innerText = '📋 MWL-ში რეგისტრაცია';
            filterByModality('');
            document.getElementById('registerModal').style.display = 'block';
            // ფოკუსი lookup ველზე
            setTimeout(() => document.getElementById('lookup_pid').focus(), 100);
        }}

        function filterByModality(modVal) {{
            const aeSelect   = document.getElementById('reg_ae_title');
            const procSelect = document.getElementById('reg_procedure');
            [aeSelect, procSelect].forEach(sel => {{
                Array.from(sel.options).forEach(opt => {{
                    if (!opt.value) return; // placeholder
                    opt.hidden = modVal ? opt.dataset.mod !== modVal : false;
                }});
                // თუ selected option დაიმალა — გადავაყენოთ placeholder-ზე
                if (sel.selectedOptions[0] && sel.selectedOptions[0].hidden) sel.value = '';
            }});
        }}

        function openRegister(name, pid, dob, mod) {{
            regData = {{ name, pid, dob, mod }};
            const modVal = mod.split(',')[0].trim();

            // lookup ველის გასუფთავება
            document.getElementById('lookup_pid').value      = pid;
            document.getElementById('lookup_status').innerHTML = '<div class="lookup-status lookup-found">✅ პაციენტი worklist-იდან</div>';

            // პაციენტის მონაცემები ავტომატურად
            document.getElementById('reg_p_name').value  = name;
            document.getElementById('reg_p_id').value    = pid;
            document.getElementById('reg_p_dob').value   = dob;
            document.getElementById('reg_p_sex').value   = '';

            // კვლევის მონაცემები
            document.getElementById('reg_modality').value   = modVal;
            document.getElementById('reg_procedure').value  = '';
            document.getElementById('reg_referring').value  = docName;
            document.getElementById('reg_ae_title').value   = '';
            document.getElementById('reg_sched_dt').value   = new Date().toISOString().slice(0,16);
            document.getElementById('reg_result').innerHTML = '';
            document.getElementById('reg_submit').disabled  = false;
            document.getElementById('reg_submit').innerText = '📋 MWL-ში რეგისტრაცია';

            // მოდალობის მიხედვით ფილტრაცია
            filterByModality(modVal);

            document.getElementById('registerModal').style.display = 'block';
        }}

        // მოდალობის dropdown-ის ცვლილებაზეც ფილტრი
        document.addEventListener('DOMContentLoaded', () => {{
            document.getElementById('reg_modality').addEventListener('change', function() {{
                filterByModality(this.value);
            }});
        }});

        async function submitRegister() {{
            const btn = document.getElementById('reg_submit');
            btn.disabled  = true;
            btn.innerText = 'იგზავნება...';

            const payload = {{
                p_name:     document.getElementById('reg_p_name').value.trim(),
                p_id:       document.getElementById('reg_p_id').value.trim(),
                p_dob:      document.getElementById('reg_p_dob').value.trim(),
                p_sex:      document.getElementById('reg_p_sex').value.trim(),
                modality:   document.getElementById('reg_modality').value.trim(),
                ae_title:   document.getElementById('reg_ae_title').value.trim(),
                procedure:  document.getElementById('reg_procedure').value.trim(),
                referring:  document.getElementById('reg_referring').value.trim(),
                sched_dt:   document.getElementById('reg_sched_dt').value.trim(),
            }};

            try {{
                const res  = await fetch('/doctor/mwl/register', {{
                    method:      'POST',
                    credentials: 'include',
                    headers:     {{'Content-Type': 'application/json'}},
                    body:        JSON.stringify(payload)
                }});
                const data = await res.json();
                const el   = document.getElementById('reg_result');
                if (res.ok) {{
                    el.innerHTML = '<div style="background:#f0fdf4;color:#166534;padding:12px 16px;border-radius:14px;font-weight:800;font-size:12px;margin-top:16px;">✅ წარმატებით დარეგისტრირდა!<br><span style=\"font-weight:600;font-size:11px;\">Accession: ' + data.accession + '</span></div>';
                    btn.innerText = '✅ დასრულდა';
                }} else {{
                    el.innerHTML = '<div style="background:#fef2f2;color:#991b1b;padding:12px 16px;border-radius:14px;font-weight:700;font-size:12px;margin-top:16px;">❌ ' + (data.detail || 'შეცდომა') + '</div>';
                    btn.disabled  = false;
                    btn.innerText = '📋 MWL-ში რეგისტრაცია';
                }}
            }} catch(e) {{
                document.getElementById('reg_result').innerHTML = '<div style="background:#fef2f2;color:#991b1b;padding:12px 16px;border-radius:14px;font-weight:700;font-size:12px;margin-top:16px;">❌ კავშირის შეცდომა</div>';
                btn.disabled  = false;
                btn.innerText = '📋 MWL-ში რეგისტრაცია';
            }}
        }}
        </script>

        <!-- ✨ MWL Registration Modal -->
        <div id="registerModal" class="modal">
            <div class="reg-modal-content">
                <button onclick="document.getElementById('registerModal').style.display='none'"
                    style="position:absolute;top:20px;right:25px;background:none;border:none;font-size:20px;cursor:pointer;color:#94a3b8;">✕</button>

                <h2 style="color:var(--dark);font-weight:900;margin:0 0 4px 0;font-size:1.4rem;">📋 MWL რეგისტრაცია</h2>
                <p style="color:#94a3b8;font-size:11px;font-weight:600;margin:0 0 12px 0;">Modality Worklist — dcm4chee-arc</p>

                <!-- ✨ პაციენტის ძებნა პირადი ნომრით -->
                <div class="lookup-box">
                    <span style="font-size:16px;">🔍</span>
                    <input id="lookup_pid" class="lookup-input" placeholder="პირადი ნომრით ძებნა (PACS-ში)..." maxlength="20"
                           onkeydown="if(event.key==='Enter'){{lookupPatient()}}">
                    <button class="lookup-btn" onclick="lookupPatient()">ძებნა</button>
                </div>
                <div id="lookup_status"></div>

                <div class="reg-grid">
                    <div class="reg-section">👤 პაციენტის მონაცემები</div>

                    <div class="reg-field full">
                        <span class="reg-label">სახელი და გვარი</span>
                        <input id="reg_p_name" class="reg-input">
                    </div>
                    <div class="reg-field">
                        <span class="reg-label">პირადი ნომერი</span>
                        <input id="reg_p_id" class="reg-input">
                    </div>
                    <div class="reg-field">
                        <span class="reg-label">დაბადების თარიღი</span>
                        <input id="reg_p_dob" class="reg-input">
                    </div>
                    <div class="reg-field full">
                        <span class="reg-label">სქესი</span>
                        <select id="reg_p_sex" class="reg-input">
                            <option value="">— აირჩიეთ —</option>
                            <option value="M">მამრობითი (M)</option>
                            <option value="F">მდედრობითი (F)</option>
                            <option value="O">სხვა (O)</option>
                        </select>
                    </div>

                    <div class="reg-section">🔬 კვლევის მონაცემები</div>

                    <div class="reg-field">
                        <span class="reg-label">მოდალობა</span>
                        <select id="reg_modality" class="reg-input">
                            <option value="CT">CT</option>
                            <option value="MR">MR</option>
                            <option value="US">US</option>
                            <option value="CR">CR</option>
                            <option value="DX">DX</option>
                            <option value="RF">RF</option>
                            <option value="ES">ES — ენდოსკოპია</option>
                            <option value="XC">XC</option>
                            <option value="XA">XA</option>
                        </select>
                    </div>
                    <div class="reg-field">
                        <span class="reg-label">AE Title (მოწყობილობა)</span>
                        <select id="reg_ae_title" class="reg-input">
                            <option value="">— აირჩიეთ —</option>
                            {ae_options}
                        </select>
                    </div>
                    <div class="reg-field full">
                        <span class="reg-label">Procedure / კვლევის აღწერა</span>
                        <select id="reg_procedure" class="reg-input">
                            <option value="">— აირჩიეთ —</option>
                            {proc_options}
                        </select>
                    </div>
                    <div class="reg-field full">
                        <span class="reg-label">დამნიშვნელი ექიმი (Referring Physician)</span>
                        <input id="reg_referring" class="reg-input" placeholder="ექიმის სახელი">
                    </div>
                    <div class="reg-field full">
                        <span class="reg-label">დაგეგმილი თარიღი და დრო</span>
                        <input id="reg_sched_dt" type="datetime-local" class="reg-input">
                    </div>
                </div>

                <div id="reg_result"></div>
                <button id="reg_submit" class="btn-reg" onclick="submitRegister()">📋 MWL-ში რეგისტრაცია</button>
            </div>
        </div>

        <!-- ✨ DICOM Upload Modal -->
        <div id="upload-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:9999;overflow-y:auto;">
          <div style="background:white;margin:3% auto;padding:2.5rem 2.5rem;border-radius:28px;width:680px;max-width:95vw;
                      border-top:6px solid #0369a1;box-shadow:0 30px 60px rgba(0,0,0,0.3);position:relative;max-height:90vh;overflow-y:auto;">
            <button onclick="closeUpload()" style="position:absolute;top:1.2rem;right:1.4rem;background:none;border:none;
                    font-size:22px;cursor:pointer;color:#94a3b8;">✕</button>

            <h2 style="font-size:1.2rem;font-weight:900;color:#003366;margin-bottom:4px;">⬆ DICOM კვლევის ატვირთვა</h2>
            <p style="font-size:12px;color:#94a3b8;margin-bottom:20px;" id="up-patient-label">
              PACS არქივში ატვირთვა · .dcm, ZIP, ფოლდერი · DICOMDIR საჭირო არ არის
            </p>

            <!-- წყაროს არჩევა -->
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:20px;">
              <button onclick="chooseSource('files')" id="src-files"
                style="padding:14px 8px;border-radius:12px;border:2px solid #bfdbfe;background:#eff6ff;
                       color:#1e40af;font-weight:700;font-size:12px;cursor:pointer;transition:all 0.2s;">
                💿 CD / USB<br><span style="font-weight:400;font-size:11px;color:#64748b;">.dcm ფაილები</span>
              </button>
              <button onclick="chooseSource('zip')" id="src-zip"
                style="padding:14px 8px;border-radius:12px;border:2px solid #e2e8f0;background:#f8fafc;
                       color:#64748b;font-weight:700;font-size:12px;cursor:pointer;transition:all 0.2s;">
                🗜 ZIP არქივი<br><span style="font-weight:400;font-size:11px;color:#64748b;">DICOM CD zip</span>
              </button>
              <button onclick="chooseSource('folder')" id="src-folder"
                style="padding:14px 8px;border-radius:12px;border:2px solid #e2e8f0;background:#f8fafc;
                       color:#64748b;font-weight:700;font-size:12px;cursor:pointer;transition:all 0.2s;">
                📁 ფოლდერი<br><span style="font-weight:400;font-size:11px;color:#64748b;">მთელი დირექტ.</span>
              </button>
            </div>

            <!-- hidden file inputs — სამი ცალ-ცალკე -->
            <input type="file" id="inp-files"  multiple accept=".dcm" style="display:none;"
                   onchange="onFilesChosen(this.files,'dcm')">
            <input type="file" id="inp-zip"    accept=".zip" style="display:none;"
                   onchange="onFilesChosen(this.files,'zip')">
            <input type="file" id="inp-folder" multiple webkitdirectory style="display:none;"
                   onchange="onFilesChosen(this.files,'folder')">

            <!-- Drop zone -->
            <div id="up-dropzone"
                 style="border:2px dashed #bfdbfe;border-radius:14px;padding:2rem;text-align:center;
                        background:#f8fafc;margin-bottom:16px;cursor:pointer;"
                 ondragover="event.preventDefault();this.style.borderColor='#0369a1'"
                 ondragleave="this.style.borderColor='#bfdbfe'"
                 ondrop="onDrop(event)"
                 onclick="triggerActive()">
              <div style="font-size:32px;margin-bottom:8px;">📂</div>
              <div style="font-weight:700;color:#003366;font-size:14px;">ჩამოაგდეთ ან დააწკაპუნეთ</div>
              <div style="font-size:11px;color:#94a3b8;margin-top:4px;">
                .dcm ფაილები · ZIP · მთელი ფოლდერი (webkitdirectory)
              </div>
            </div>

            <!-- ფაილების სია / progress -->
            <div id="up-filelist" style="margin-bottom:14px;"></div>

            <!-- პაციენტის ID -->
            <div style="margin-bottom:14px;">
              <label style="font-size:10px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:1.5px;display:block;margin-bottom:6px;">
                პაციენტის ID <span style="color:#94a3b8;font-weight:400;">(pre-fill-ი worklist-იდან ან შეიყვანეთ)</span>
              </label>
              <input id="up-pid" type="text" placeholder="01234567890"
                     style="width:100%;border:1.5px solid #e2e8f0;border-radius:10px;padding:10px 14px;
                            font-size:13px;outline:none;box-sizing:border-box;background:#f8fafc;">
            </div>

            <!-- წყარო / შენიშვნა -->
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px;">
              <div>
                <label style="font-size:10px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:1.5px;display:block;margin-bottom:6px;">კვლევის წყარო</label>
                <input id="up-src" type="text" placeholder="სამედიცინო ცენტრი X"
                       style="width:100%;border:1.5px solid #e2e8f0;border-radius:10px;padding:10px 14px;
                              font-size:13px;outline:none;box-sizing:border-box;background:#f8fafc;">
              </div>
              <div>
                <label style="font-size:10px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:1.5px;display:block;margin-bottom:6px;">შენიშვნა</label>
                <input id="up-note" type="text" placeholder="CT გულ-მკერდი 2024-01-15"
                       style="width:100%;border:1.5px solid #e2e8f0;border-radius:10px;padding:10px 14px;
                              font-size:13px;outline:none;box-sizing:border-box;background:#f8fafc;">
              </div>
            </div>

            <!-- info -->
            <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:10px 14px;margin-bottom:16px;font-size:12px;color:#0c4a6e;">
              ℹ️ ატვირთვის შემდეგ კვლევა გამოჩნდება Worklist-ში (STOW-RS).
              PatientID-ით ხდება merge PACS-ის ჩანაწერთან. DICOMDIR საჭირო არ არის.
            </div>

            <button id="up-btn" onclick="submitUpload()"
                    style="width:100%;background:#0369a1;color:white;border:none;border-radius:10px;
                           padding:13px;font-size:13px;font-weight:700;cursor:pointer;">
              ⬆ PACS-ში ატვირთვა
            </button>
            <div id="up-result" style="margin-top:12px;"></div>
          </div>
        </div>

    </body></html>"""

    # dropdown options ჩასმა (f-string ვერ ამუშავებს, ამიტომ replace)
    html = html.replace("{ae_options}",   ae_options)
    html = html.replace("{proc_options}", proc_options)

    return HTMLResponse(content=html, headers={"Cache-Control": "no-store, must-revalidate"})

# ==========================================================
# 6. ZIP გადმოწერა
# ==========================================================
@app.get("/p/download-zip/{uid}/auto")
async def download_zip(uid: str, doctor_token: str = Cookie(None), patient_auth: str = Cookie(None)):
    is_doctor  = bool(verify_doctor_token(doctor_token))
    is_patient = (patient_auth == uid)  # პაციენტმა უკვე გაიარა PID+DOB ვერიფიკაცია სწორედ ამ study-ზე
    if not (is_doctor or is_patient):
        return RedirectResponse(url=f"/p/{uid}")  # გაუშვი პაციენტის ვერიფიკაციის გვერდზე

    final_name = f"Innova_Study_{uid[:8]}.zip"
    try:
        m_res = requests.get(f"{PACS_URL}/studies?StudyInstanceUID={uid}",
                             headers={'Accept': 'application/json'}, timeout=5)
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
        print(f"Filename error: {e}")

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
                    <a href="{body_final}" target="_blank"
                       style="background:#004a99;color:white;padding:15px 25px;text-decoration:none;border-radius:8px;font-weight:bold;">
                       კვლევის ნახვა
                    </a>
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
# 10. პაციენტის ძებნა PACS-ში პირადი ნომრით (MWL lookup)
# ==========================================================
@app.get("/doctor/mwl/lookup-patient")
async def lookup_patient(request: Request, pid: str):
    # cookie პირდაპირ request-იდან — httponly cookie-ებისთვის ყველაზე საიმედო
    token = request.cookies.get("doctor_token")
    if not token:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    try:
        r = requests.get(
            f"{PACS_URL}/studies",
            params={
                "PatientID":    pid,
                "includefield": "all",
                "limit":        1,
                "StudyDate":    "20000101-",
            },
            headers={"Accept": "application/json"},
            timeout=8
        )
        if r.status_code == 200 and r.json():
            s        = r.json()[0]
            raw_name = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "")
            p_name   = raw_name.replace("^", " ").strip()
            p_id     = str(s.get("00100020", {}).get("Value", [""])[0]).strip()
            dob_raw  = str(s.get("00100030", {}).get("Value", [""])[0]).strip()
            sex      = str(s.get("00100040", {}).get("Value", [""])[0]).strip()
            f_dob    = f"{dob_raw[:4]}-{dob_raw[4:6]}-{dob_raw[6:8]}" if len(dob_raw) == 8 else dob_raw
            return {"found": True, "name": p_name, "pid": p_id, "dob": f_dob, "sex": sex}

        return {"found": False}

    except Exception as e:
        print(f"❌ lookup-patient error: {e}")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": str(e)})

# ==========================================================
# 9. MWL რეგისტრაცია (Modality Worklist — dcm4chee-arc 5.33)
# ==========================================================
@app.post("/doctor/mwl/register")
async def mwl_register(request: Request):
    token = request.cookies.get("doctor_token")
    if not token:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    try:
        payload = await request.json()
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})

    try:
        # ── Accession Number: ავტომატური (timestamp + pid suffix)
        import uuid as _uuid
        accession = datetime.datetime.now().strftime("%Y%m%d%H%M%S") + payload.get("p_id","")[-4:]

        # ── Scheduled DateTime → DICOM ფორმატი YYYYMMDDHHMMSS
        sched_raw = payload.get("sched_dt", "")
        try:
            sched_dt = datetime.datetime.fromisoformat(sched_raw)
            sched_date = sched_dt.strftime("%Y%m%d")
            sched_time = sched_dt.strftime("%H%M%S")
        except:
            sched_date = datetime.datetime.now().strftime("%Y%m%d")
            sched_time = datetime.datetime.now().strftime("%H%M%S")

        # ── პაციენტის სახელი → DICOM format: SURNAME^FIRSTNAME
        p_name_raw = payload.get("p_name", "").strip()
        if " " in p_name_raw:
            parts = p_name_raw.split(" ", 1)
            dicom_name = f"{parts[0].upper()}^{parts[1].upper()}"
        else:
            dicom_name = p_name_raw.upper()

        # ── DOB → YYYYMMDD
        dob_raw = payload.get("p_dob", "").replace("-", "")

        # ── MWL DICOM JSON — dcm4chee-arc 5.33 ფორმატი
        mwl_body = {
            # Patient Level
            "00100010": {"vr": "PN", "Value": [{"Alphabetic": dicom_name}]},
            "00100020": {"vr": "LO", "Value": [payload.get("p_id", "")]},
            "00100030": {"vr": "DA", "Value": [dob_raw]},
            "00100040": {"vr": "CS", "Value": [payload.get("p_sex", "O")]},

            # Study / Request Level
            "00080050": {"vr": "SH", "Value": [accession]},           # Accession Number
            "00080090": {"vr": "PN", "Value": [{"Alphabetic": payload.get("referring", "").upper()}]},  # Referring Physician
            "00321060": {"vr": "LO", "Value": [payload.get("procedure", "")]},  # Requested Procedure Description
            "00401001": {"vr": "SH", "Value": [accession]},           # Requested Procedure ID

            # Scheduled Procedure Step Sequence (0040,0100)
            "00400100": {"vr": "SQ", "Value": [{
                "00400001": {"vr": "AE", "Value": [payload.get("ae_title", AE_TITLE)]},  # Scheduled Station AE Title
                "00400002": {"vr": "DA", "Value": [sched_date]},       # Scheduled Procedure Step Start Date
                "00400003": {"vr": "TM", "Value": [sched_time]},       # Scheduled Procedure Step Start Time
                "00080060": {"vr": "CS", "Value": [payload.get("modality", "CT")]},      # Modality
                "00400006": {"vr": "PN", "Value": [{"Alphabetic": payload.get("referring", "").upper()}]},  # Scheduled Performing Physician
                "00400007": {"vr": "LO", "Value": [payload.get("procedure", "")]},       # Scheduled Procedure Step Description
                "00400009": {"vr": "SH", "Value": [accession]},        # Scheduled Procedure Step ID
                "00400010": {"vr": "SH", "Value": [payload.get("ae_title", AE_TITLE)]}, # Scheduled Station Name
                "00400020": {"vr": "CS", "Value": ["SCHEDULED"]},      # Scheduled Procedure Step Status
            }]},
        }

        # ── dcm4chee-arc 5.33 MWL შექმნა
        # სწორი endpoint: POST /aets/{AET}/rs/mwlitems  (body = JSON array)
        mwl_url = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs/mwlitems"

        r = requests.post(
            mwl_url,
            json=[mwl_body],          # ← array-ში გაგზავნა სავალდებულოა
            headers={
                "Content-Type": "application/dicom+json",
                "Accept":       "application/json",
            },
            timeout=10
        )

        print(f"MWL response: {r.status_code} — {r.text[:300]}")

        if r.status_code in (200, 201, 204):
            print(f"✅ MWL registered: {accession} — {dicom_name}")
            return {"status": "ok", "accession": accession}
        else:
            print(f"❌ MWL error {r.status_code}: {r.text[:300]}")
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=502, content={"detail": f"PACS {r.status_code}: {r.text[:300]}"})

    except Exception as e:
        print(f"❌ MWL register exception: {e}")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": str(e)})

# ==========================================================
# 8. პაციენტის პორტალი
# ==========================================================
@app.get("/p/{study_uid}", response_class=HTMLResponse)
async def patient_login_page(study_uid: str, lang: str = "ka"):
    html = f"""<html><head>{STYLE}<title>{T(lang,'app_title')}</title></head>
    <body class="login-body">
        <form action="/p/verify" method="post" class="login-card">
            <h1 class="h-title">INNOVA <span>PACS</span></h1>
            <p style="font-size:9px;font-weight:800;color:#94a3b8;letter-spacing:2px;margin:10px 0 30px 0;">{T(lang,'patient_subtitle')}</p>
            <input type="hidden" name="uid"  value="{study_uid}">
            <input type="hidden" name="lang" value="{lang}">
            <div style="margin-bottom:15px;text-align:left;">
                <label>{T(lang,'patient_pid_lbl')}</label>
                <input type="text" name="pid" placeholder="{T(lang,'patient_pid_ph')}" class="in-input" required>
            </div>
            <div style="margin-bottom:30px;text-align:left;">
                <label>{T(lang,'patient_dob_lbl')}</label>
                <input type="text" name="dob" placeholder="{T(lang,'patient_dob_ph')}" class="in-input" required>
            </div>
            <button type="submit" class="btn btn-green" style="width:100%;padding:14px;font-size:13px;">{T(lang,'patient_view_btn')}</button>
            <p style="font-size:10px;color:#999;margin-top:20px;font-style:italic;">{T(lang,'patient_hint')}</p>
            <div style="margin-top:15px;display:flex;justify-content:center;gap:8px;background:#f1f5f9;padding:6px;border-radius:20px;width:fit-content;margin-left:auto;margin-right:auto;">
                <a href="/p/{study_uid}?lang=ka" style="padding:5px 14px;border-radius:16px;font-size:11px;font-weight:800;text-decoration:none;{'background:var(--dark);color:white;' if lang=='ka' else 'color:#64748b;'}">KA</a>
                <a href="/p/{study_uid}?lang=en" style="padding:5px 14px;border-radius:16px;font-size:11px;font-weight:800;text-decoration:none;{'background:var(--dark);color:white;' if lang=='en' else 'color:#64748b;'}">EN</a>
            </div>
        </form>
    </body></html>"""
    return HTMLResponse(content=html)

@app.post("/p/verify")
async def verify_patient(uid: str = Form(...), pid: str = Form(...),
                         dob: str = Form(...), lang: str = Form("ka")):
    try:
        r = requests.get(f"{PACS_URL}/studies",
                         params={"StudyInstanceUID": uid},
                         headers={"Accept": "application/json"}, timeout=10)
        if r.status_code == 200 and r.json():
            data     = r.json()[0]
            real_pid = str(data.get("00100020", {}).get("Value", [""])[0]).strip()
            real_dob = str(data.get("00100030", {}).get("Value", [""])[0]).strip()
            u_pid    = "".join(filter(str.isdigit, pid))
            u_dob    = "".join(filter(str.isdigit, dob))
            if u_pid == real_pid and u_dob == real_dob:
                resp = RedirectResponse(url=f"/viewer?StudyInstanceUIDs={uid}", status_code=303)
                resp.set_cookie(key="patient_auth", value=uid, max_age=3600,
                                path="/", domain=DOMAIN_NAME, secure=True)
                return resp
        return HTMLResponse(f"<script>alert('{T(lang,'patient_err')}'); window.history.back();</script>")
    except Exception as e:
        return HTMLResponse(f"{T(lang,'patient_verify_err')}: {str(e)}")


# ══════════════════════════════════════════════════════════════
# DICOM Upload — /doctor/upload/submit
# .dcm, ZIP, ISO, ფოლდერი — DICOMDIR საჭირო არ არის
# ══════════════════════════════════════════════════════════════
import zipfile, io, struct, csv as _csv, os as _os

def _parse_dicom_meta(data: bytes) -> dict:
    """DICOM ფაილიდან სწრაფი header წაკითხვა (pydicom-ის გარეშე)."""
    result = {}
    if len(data) < 132:
        return result
    offset = 132 if data[128:132] == b'DICM' else 0
    _TAGS = {
        (0x0010,0x0010): "PatientName",
        (0x0010,0x0020): "PatientID",
        (0x0010,0x0030): "PatientBirthDate",
        (0x0008,0x0020): "StudyDate",
        (0x0008,0x0060): "Modality",
        (0x0008,0x1030): "StudyDescription",
        (0x0008,0x0050): "AccessionNumber",
        (0x0020,0x000D): "StudyInstanceUID",
        (0x0020,0x000E): "SeriesInstanceUID",
        (0x0008,0x0080): "InstitutionName",
    }
    _LONG = {"OB","OW","SQ","UC","UN","UR","UT","OD","OF","OL","SV","UV"}
    _VRS  = {"AE","AS","AT","CS","DA","DS","DT","FL","FD","IS","LO","LT",
              "OB","OD","OF","OL","OW","PN","SH","SL","SQ","SS","ST","SV",
              "TM","UC","UI","UL","UN","UR","US","UT","UV"}
    pos = offset
    while pos + 8 <= len(data) and len(result) < 12:
        try:
            g  = int.from_bytes(data[pos:pos+2],   "little")
            e  = int.from_bytes(data[pos+2:pos+4], "little")
            vr = data[pos+4:pos+6].decode("ascii", errors="replace")
            if vr in _VRS:
                if vr in _LONG:
                    ln = int.from_bytes(data[pos+8:pos+12], "little"); vs = pos+12
                else:
                    ln = int.from_bytes(data[pos+6:pos+8], "little"); vs = pos+8
            else:
                ln = int.from_bytes(data[pos+4:pos+8], "little"); vs = pos+8
            if ln in (0xFFFFFFFF, 0xFFFFFFFE) or ln < 0:
                pos += 8; continue
            ve = vs + ln
            if ve > len(data): break
            tag = (g, e)
            if tag in _TAGS:
                val = data[vs:ve].decode("latin-1", errors="ignore").strip().rstrip("\x00").strip()
                if val:
                    result[_TAGS[tag]] = val
            pos = ve + (ve % 2)
        except:
            pos += 2
    return result


def _extract_dcm_bytes(data: bytes, filename: str) -> list:
    """ფაილიდან (dcm/zip/iso) ყველა DICOM bytes-ის ამოღება სიის სახით."""
    fname = filename.lower()
    out   = []

    # ── ZIP ──────────────────────────────────────────────────
    if fname.endswith(".zip") or data[:4] == b'PK\x03\x04':
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                raw = zf.read(name)
                # DICOM magic ან extension-ის გარეშე ფაილი
                if (len(raw) > 132 and raw[128:132] == b'DICM') or \
                   name.lower().endswith(".dcm") or \
                   ("." not in name.split("/")[-1] and len(raw) > 256):
                    out.append((name, raw))
        except Exception as ex:
            print(f"zip extract error: {ex}")
        return out

    # ── ISO 9660 ─────────────────────────────────────────────
    if fname.endswith(".iso") or (len(data) > 0x8006 and data[0x8001:0x8006] == b'CD001'):
        try:
            SECT = 2048
            pvd  = 0x8000
            root_lba  = int.from_bytes(data[pvd+156+2:pvd+156+6],   "little")
            root_size = int.from_bytes(data[pvd+156+10:pvd+156+14], "little")
            def walk(lba, size):
                pos = lba * SECT
                end = pos + size
                while pos < end and pos + 1 < len(data):
                    rl = data[pos]
                    if rl == 0:
                        pos = ((pos // SECT) + 1) * SECT; continue
                    if pos + rl > len(data): break
                    flags    = data[pos+25]
                    nl       = data[pos+32]
                    nm       = data[pos+33:pos+33+nl].decode("ascii","replace").split(";")[0].strip()
                    flba     = int.from_bytes(data[pos+2:pos+6],   "little")
                    fsize    = int.from_bytes(data[pos+10:pos+14], "little")
                    is_dir   = bool(flags & 0x02)
                    is_dot   = nm in ("", "\x00", "\x01")
                    if not is_dot:
                        if is_dir:
                            walk(flba, fsize)
                        else:
                            un = nm.upper()
                            if un.endswith(".DCM") or ("." not in un and un != "DICOMDIR" and fsize > 256):
                                s = flba * SECT
                                raw = data[s:s+fsize]
                                if len(raw) > 132 and raw[128:132] == b'DICM':
                                    out.append((nm, raw))
                    pos += rl
            walk(root_lba, root_size)
        except Exception as ex:
            print(f"iso extract error: {ex}")
        return out

    # ── .dcm ─────────────────────────────────────────────────
    if fname.endswith(".dcm") or (len(data) > 132 and data[128:132] == b'DICM'):
        out.append((filename, data))
    return out


def _stow_rs_innova(dcm_list: list) -> dict:
    """STOW-RS → dcm4chee."""
    if not dcm_list:
        return {"ok": False, "error": "ფაილები ცარიელია"}
    boundary = "InnovaUpload"
    parts    = []
    for name, raw in dcm_list:
        hdr = (f"--{boundary}\r\n"
               f"Content-Type: application/dicom\r\n"
               f"Content-Disposition: form-data; name=\"file\"; "
               f"filename=\"{os.path.basename(name)}\"\r\n\r\n").encode()
        parts.append(hdr + raw + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    try:
        r = requests.post(
            f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs/studies",
            data=body,
            headers={"Content-Type": f"multipart/related; type=\"application/dicom\"; boundary={boundary}"},
            timeout=300
        )
        if r.status_code in (200, 201):
            return {"ok": True, "instances": len(dcm_list)}
        return {"ok": False, "error": f"PACS {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _log_dicom_upload(doctor: str, pid: str, source: str, instances: int, status: str):
    log_path = os.path.join(os.path.dirname(__file__), "upload_log.csv")
    try:
        exists = os.path.exists(log_path)
        with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            if not exists:
                w.writerow(["დრო","ექიმი","პაციენტი","წყარო","Instances","სტატუსი"])
            w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        doctor, pid, source, instances, status])
    except:
        pass


@app.post("/doctor/upload/submit")
async def upload_submit(
    request:      Request,
    doctor_token: str = Cookie(None),
    doc_full_name:str = Cookie("Doctor"),
):
    if not verify_doctor_token(doctor_token):
        return JSONResponse({"status": "error", "detail": "Unauthorized"}, status_code=401)

    try:
        form         = await request.form()
        files        = form.getlist("files")
        source       = str(form.get("source",       "")).strip()
        override_pid = str(form.get("override_pid", "")).strip()

        if not files:
            return JSONResponse({"status": "error", "detail": "ფაილი არ მიღებულა"})

        all_dcm = []
        for f in files:
            try:
                raw  = await f.read()
                if not raw:
                    continue
                dcms = _extract_dcm_bytes(raw, getattr(f, "filename", None) or "file.dcm")
                all_dcm.extend(dcms)
            except Exception as fe:
                print(f"file read error: {fe}")

        if not all_dcm:
            return JSONResponse({"status": "error",
                                 "detail": ".dcm ფაილები ვერ მოიძებნა. DICOM ფაილებია?"})

        # study-ების დაჯგუფება uid-ით
        studies: dict = {}
        for name, raw in all_dcm:
            meta = _parse_dicom_meta(raw)
            uid  = meta.get("StudyInstanceUID") or "ungrouped"
            if uid not in studies:
                studies[uid] = {
                    "files": [],
                    "pid":   meta.get("PatientID", ""),
                    "name":  meta.get("PatientName", "").replace("^", " ").strip(),
                }
            studies[uid]["files"].append((name, raw))

        total_instances = 0
        study_count     = 0
        errors          = []

        for uid, s in studies.items():
            res     = _stow_rs_innova(s["files"])
            pid_log = override_pid or s["pid"]
            if res["ok"]:
                total_instances += res["instances"]
                study_count     += 1
                _log_dicom_upload(doc_full_name, pid_log, source, res["instances"], "ok")
            else:
                errors.append(res["error"])
                _log_dicom_upload(doc_full_name, pid_log, source, 0, f"error: {res['error'][:50]}")

        if errors and not study_count:
            return JSONResponse({"status": "error", "detail": errors[0]})

        return JSONResponse({
            "status":          "ok",
            "total_instances": total_instances,
            "studies":         study_count,
            "errors":          errors,
        })

    except Exception as e:
        print(f"upload_submit error: {e}")
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


# ==========================================================
# USB Token — one-time download token გენერაცია
# ==========================================================
@app.get("/doctor/usb-token/{uid}")
async def usb_get_token(uid: str, doctor_token: str = Cookie(None)):
    if not verify_doctor_token(doctor_token):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    # one-time token — 10 წუთი
    token = secrets.token_hex(32)  # hex — მხოლოდ 0-9 a-f, / არ არის
    expires = datetime.datetime.now() + datetime.timedelta(minutes=10)
    _usb_tokens[token] = (uid, expires)
    return {"token": token, "uid": uid}


# ==========================================================
# USB/CD Export — /doctor/usb-export/{uid}
# ZIP: DICOM/*.dcm + autorun.inf + README.txt
# RadiAnt-ის portable ვერსია მომხმარებელმა ჩადოს RadiAnt/
# ==========================================================
import zipfile as _zf
import io as _io

@app.get("/doctor/usb-export/{uid}")
async def usb_export(
    uid: str,
    doctor_token: str = Cookie(None),
    token: str = "",          # URL parameter-ით გადაცემა exe-სთვის
):
    # one-time download token შემოწმება
    if token and token in _usb_tokens:
        uid_check, expires = _usb_tokens[token]
        if datetime.datetime.now() < expires:
            del _usb_tokens[token]  # ერთხელ გამოყენება
            # OK — გაგრძელება
        else:
            del _usb_tokens[token]
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "token expired"}, status_code=401)
    elif not verify_doctor_token(doctor_token):
        return RedirectResponse(url="/login")

    # პაციენტის მეტა
    final_name = f"USB_Study_{uid[:8]}.zip"
    patient_name = "Patient"
    patient_id   = ""
    study_date   = ""
    modality     = "STUDY"

    try:
        m_res = requests.get(
            f"{PACS_URL}/studies?StudyInstanceUID={uid}",
            headers={"Accept": "application/json"}, timeout=5
        )
        if m_res.status_code == 200 and m_res.json():
            m = m_res.json()[0]
            study_date   = m.get("00080020", {}).get("Value", [""])[0]
            patient_id   = str(m.get("00100020", {}).get("Value", [""])[0])
            mod_list     = m.get("00080061", {}).get("Value", m.get("00080060", {}).get("Value", ["STUDY"]))
            modality     = str(mod_list[0]).replace("/", "-")
            raw_name     = m.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "P^Name")
            patient_name = raw_name.replace("^", " ").strip()
            if "^" in raw_name:
                parts    = raw_name.split("^")
                surname  = parts[0].strip()
                initial  = parts[1][0].upper() if len(parts) > 1 and parts[1] else "P"
                name_part = f"{initial}_{surname}"
            else:
                name_part = patient_name.replace(" ", "_")
            final_name = f"USB_{study_date}_{name_part}_{modality}_{patient_id}.zip"
    except Exception as e:
        print(f"USB export meta error: {e}")

    # autorun.inf — RadiAnt CD/DVD სტანდარტი
    autorun = (
        "[AutoRun.Amd64]\r\n"
        f"LABEL={CLINIC_NAME}\r\n"
        "ICON=RA64\\viewer64.exe,0\r\n"
        "OPEN=RA64\\viewer64.exe -d DICOM\r\n"
        "\r\n"
        "[AutoRun]\r\n"
        f"LABEL={CLINIC_NAME}\r\n"
        "ICON=RA32\\viewer32.exe,0\r\n"
        "OPEN=RA32\\viewer32.exe -d DICOM\r\n"
    )

    # README.txt
    fd = f"{study_date[:4]}-{study_date[4:6]}-{study_date[6:8]}" if len(study_date)==8 else study_date
    readme = (
        f"სამედიცინო კვლევა — {CLINIC_NAME}\r\n"
        f"{'='*40}\r\n"
        f"პაციენტი : {patient_name}\r\n"
        f"პირადი N  : {patient_id}\r\n"
        f"თარიღი   : {fd}\r\n"
        f"მოდალობა : {modality}\r\n"
        f"\r\n"
        f"გამოყენება:\r\n"
        f"  1. RadiAnt Viewer ფოლდერი ჩადეთ ფლეშკაზე:\r\n"
        f"     USB:\\\r\n"
        f"     ├── RadiAnt\\          (RadiAnt Portable)\r\n"
        f"     ├── DICOM\\            (კვლევის ფაილები)\r\n"
        f"     ├── autorun.inf\r\n"
        f"     └── README.txt\r\n"
        f"  2. ფლეშკა ჩადეთ — RadiAnt ავტომატურად გაიხსნება\r\n"
        f"     ან: RadiAnt\\RadiAntDICOMViewer.exe გაუშვით\r\n"
        f"\r\n"
        f"RadiAnt DICOM Viewer: https://www.radiantviewer.com\r\n"
    )

    # DICOM ფაილები PACS-იდან
    pacs_zip_url = (
        f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"
        f"/studies/{uid}?accept=application/zip&dicomdir=true"
    )

    # RadiAnt portable-ის გზა სერვერზე
    RADIANT_PATH = os.path.join(os.path.dirname(__file__), "static", "RadiAnt")

    async def generate():
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, mode="w", compression=_zf.ZIP_DEFLATED, allowZip64=True) as zout:

            # სერვისური ფაილები
            zout.writestr("autorun.inf", autorun.encode("utf-8"))
            zout.writestr("README.txt",  readme.encode("utf-8"))
            zout.writestr("START.bat", (
                "@echo off\r\n"
                "cd /d \"%~dp0\"\r\n"
                "RA32\\launcher.exe -d DICOM\r\n"
            ).encode("utf-8"))

            # RadiAnt portable — root-ში (RA32/, RA64/, COMMON/)
            if os.path.isdir(RADIANT_PATH):
                for root, dirs, files in os.walk(RADIANT_PATH):
                    for fname in files:
                        full = os.path.join(root, fname)
                        # RelPath RadiAnt/-ის შიგნიდან — root-ში ვდებთ
                        rel = os.path.relpath(full, RADIANT_PATH)
                        rel = rel.replace(os.sep, "/")
                        try:
                            with open(full, "rb") as f:
                                zout.writestr(rel, f.read())
                        except Exception as ex:
                            print(f"RadiAnt file error {rel}: {ex}")
            else:
                zout.writestr(
                    "README_RADIANT.txt",
                    (
                        "RadiAnt DICOM Viewer CD/DVD portable ჩადეთ root-ში.\r\n"
                        "ჩამოტვირთვა: https://www.radiantviewer.com\r\n"
                        "ZIP შიგთავსი root-ში: RA32/, RA64/, COMMON/\r\n"
                    ).encode("utf-8")
                )

            # DICOM ფაილები — PACS-იდან, root-ში (CD mode)
            try:
                r = requests.get(pacs_zip_url, stream=True, timeout=None)
                if r.status_code == 200:
                    pacs_buf = _io.BytesIO(r.content)
                    with _zf.ZipFile(pacs_buf, "r") as zin:
                        for item in zin.infolist():
                            name = item.filename
                            # DICOMDIR root-ში, დანარჩენი DICOM/ prefix-ით
                            if name.upper() == "DICOMDIR":
                                zout.writestr("DICOMDIR", zin.read(item.filename))
                            else:
                                if not name.startswith("DICOM/"):
                                    name = "DICOM/" + name.lstrip("/")
                                zout.writestr(name, zin.read(item.filename))
                else:
                    zout.writestr("DICOM/ERROR.txt",
                                  f"PACS error: {r.status_code}".encode())
            except Exception as e:
                zout.writestr("DICOM/ERROR.txt", str(e).encode())

        buf.seek(0)
        yield buf.read()

    return StreamingResponse(
        generate(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{final_name}"'}
    )



# ==========================================================
# DEXA AI ანალიზი — /doctor/dexa-analyze/{study_uid}
# Gemini Vision-ით სურათის ანალიზი და ქართული დასკვნა
# ==========================================================
@app.get("/doctor/dexa-analyze/{study_uid}")
async def dexa_analyze(study_uid: str, doctor_token: str = Cookie(None)):
    if not verify_doctor_token(doctor_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not GEMINI_API_KEY:
        return JSONResponse({"error": "Gemini API key არ არის კონფიგურირებული"}, status_code=500)

    try:
        # Study-ის პირველი instance-ის სურათი
        series_r = requests.get(
            f"{PACS_URL}/studies/{study_uid}/series",
            headers={"Accept": "application/json"}, timeout=10
        )
        if series_r.status_code != 200 or not series_r.json():
            return JSONResponse({"error": "Series ვერ მოიძებნა"})

        series_uid = series_r.json()[0].get("0020000E", {}).get("Value", [""])[0]

        inst_r = requests.get(
            f"{PACS_URL}/studies/{study_uid}/series/{series_uid}/instances",
            headers={"Accept": "application/json"}, timeout=10
        )
        if inst_r.status_code != 200 or not inst_r.json():
            return JSONResponse({"error": "Instance ვერ მოიძებნა"})

        instance_uid = inst_r.json()[0].get("00080018", {}).get("Value", [""])[0]

        # WADO-URI-ით სურათის ჩამოქაჩვა
        wado_url = (
            f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/wado"
            f"?requestType=WADO"
            f"&studyUID={study_uid}"
            f"&seriesUID={series_uid}"
            f"&objectUID={instance_uid}"
            f"&contentType=image/jpeg&rows=1200"
        )
        img_r = requests.get(wado_url, timeout=30)
        if img_r.status_code != 200:
            return JSONResponse({"error": f"სურათი ვერ ჩამოიქაჩა: {img_r.status_code}"})

        import base64 as _b64
        img_b64 = _b64.b64encode(img_r.content).decode()

        # Gemini API call
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

        prompt = """შენ ხარ სამედიცინო ექსპერტი - რადიოლოგი. გაანალიზე ეს DEXA Body Composition სკანის სურათი და დაწერე პროფესიონალური დასკვნა ქართულ ენაზე.

სურათიდან ამოიკითხე შემდეგი მონაცემები (თუ ხილვადია):
- სხეულის საერთო შემადგენლობა (Total Body Composition)
- ცხიმოვანი ქსოვილი (Fat Mass, Fat %)
- კუნთოვანი მასა (Lean Mass)
- ძვლის მინერალური შემადგენლობა (BMC/BMD)
- ვისცერული ცხიმი (Visceral Fat)
- რეგიონული მონაცემები (trunk, arms, legs)

დასკვნა დაწერე შემდეგი ფორმატით:

**სხეულის შემადგენლობის დენსიტომეტრიული კვლევის დასკვნა**

**კვლევის შედეგები:**
[ამოკითხული მონაცემები ცხრილის სახით ან სიით]

**დასკვნა:**
[კლინიკური ინტერპრეტაცია - ნორმაა თუ გადახრა, რისკ ფაქტორები]

**რეკომენდაცია:**
[პრაქტიკული რეკომენდაციები]

თუ სურათზე ციფრები ან ტექსტი ვერ იკითხება, მიუთითე ეს. დასკვნა უნდა იყოს პროფესიონალური და ქართულ ენაზე."""

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": img_b64
                        }
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 2048,
            }
        }

        gemini_r = requests.post(gemini_url, json=payload, timeout=60)
        if gemini_r.status_code != 200:
            return JSONResponse({"error": f"Gemini შეცდომა: {gemini_r.status_code} — {gemini_r.text[:200]}"})

        result = gemini_r.json()
        text = result["candidates"][0]["content"]["parts"][0]["text"]

        return JSONResponse({"conclusion": text})

    except Exception as e:
        print(f"dexa_analyze error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ==========================================================
# DEXA / BMD Report — /doctor/dexa-report/{study_uid}
# ==========================================================
@app.get("/doctor/dexa-report/{study_uid}", response_class=HTMLResponse)
async def dexa_report(
    study_uid:     str,
    doctor_token:  str = Cookie(None),
    doc_full_name: str = Cookie("Doctor"),
    lang:          str = "ka",
):
    if not verify_doctor_token(doctor_token):
        return RedirectResponse(url="/login")

    # პაციენტის მეტა PACS-იდან
    p_name = ""; p_id = ""; p_dob = ""; p_age = ""; p_sex = ""
    study_date = ""; accession = ""; study_desc = ""
    instances = []

    try:
        # Study metadata
        r = requests.get(
            f"{PACS_URL}/studies",
            params={"StudyInstanceUID": study_uid, "includefield": "all"},
            headers={"Accept": "application/json"}, timeout=10
        )
        if r.status_code == 200 and r.json():
            s = r.json()[0]
            raw_name   = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "")
            p_name     = raw_name.replace("^", " ").strip()
            p_id       = str(s.get("00100020", {}).get("Value", [""])[0])
            dob_raw    = str(s.get("00100030", {}).get("Value", [""])[0])
            p_dob      = f"{dob_raw[:4]}-{dob_raw[4:6]}-{dob_raw[6:8]}" if len(dob_raw) == 8 else dob_raw
            p_sex      = str(s.get("00100040", {}).get("Value", [""])[0])
            p_age      = str(s.get("00101010", {}).get("Value", [""])[0]).replace("Y", " წელი")
            sd_raw     = str(s.get("00080020", {}).get("Value", [""])[0])
            study_date = f"{sd_raw[:4]}-{sd_raw[4:6]}-{sd_raw[6:8]}" if len(sd_raw) == 8 else sd_raw
            accession  = str(s.get("00080050", {}).get("Value", [""])[0])
            study_desc = str((s.get("00081030", {}).get("Value") or ["BMD კვლევა"])[0])

        # Series და instances
        r2 = requests.get(
            f"{PACS_URL}/studies/{study_uid}/series",
            headers={"Accept": "application/json"}, timeout=10
        )
        if r2.status_code == 200:
            for series in r2.json():
                series_uid = series.get("0020000E", {}).get("Value", [""])[0]
                r3 = requests.get(
                    f"{PACS_URL}/studies/{study_uid}/series/{series_uid}/instances",
                    headers={"Accept": "application/json"}, timeout=10
                )
                if r3.status_code == 200:
                    for inst in r3.json():
                        iuid = inst.get("00080018", {}).get("Value", [""])[0]
                        instances.append({
                            "series_uid": series_uid,
                            "instance_uid": iuid,
                        })
    except Exception as e:
        print(f"dexa_report error: {e}")

    # WADO-URI image URLs
    wado_base = f"https://{DOMAIN_NAME}/dcm4chee-arc/aets/{AE_TITLE}/wado"
    images_html = ""
    for inst in instances:
        wado_url = (
            f"{wado_base}?requestType=WADO"
            f"&studyUID={study_uid}"
            f"&seriesUID={inst['series_uid']}"
            f"&objectUID={inst['instance_uid']}"
            f"&contentType=image/jpeg&rows=1200"
        )
        images_html += f"""
        <div class="img-wrap">
            <img src="{wado_url}" alt="DEXA სურათი"
                 onerror="this.style.display='none'"
                 onload="this.style.opacity='1'">
        </div>"""

    sex_label = {"M": "მამრობითი", "F": "მდედრობითი", "O": "სხვა"}.get(p_sex, p_sex)

    # BMI გამოთვლა (თუ PACS-ში მონაცემები არ არის, AI შეავსებს)
    study_date_fmt = study_date

    html = f"""<!DOCTYPE html>
<html lang="ka">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DEXA რეპორტი — {p_name}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Georgian:wght@400;500;600;700;900&display=swap');
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Noto Sans Georgian',sans-serif; background:#f0f4f8; color:#1a202c; font-size:13px; }}
  .page {{ max-width:900px; margin:0 auto; padding:20px; }}

  /* ══ Header ══ */
  .clinic-header {{
    background:linear-gradient(135deg,#003366 0%,#004080 100%);
    color:white; padding:20px 28px; border-radius:14px;
    display:flex; justify-content:space-between; align-items:center;
    margin-bottom:16px;
  }}
  .clinic-name {{ font-size:24px; font-weight:900; font-style:italic; letter-spacing:1px; }}
  .clinic-name span {{ color:#b1d431; }}
  .clinic-sub {{ font-size:10px; color:#93c5fd; margin-top:3px; letter-spacing:2px; font-weight:600; }}
  .report-title {{
    background:white; border:1px solid #e2e8f0; border-radius:10px;
    padding:14px 20px; margin-bottom:16px; text-align:center;
  }}
  .report-title h1 {{ font-size:15px; font-weight:800; color:#003366; margin-bottom:4px; }}
  .report-title p {{ font-size:11px; color:#64748b; }}

  /* ══ Patient card ══ */
  .patient-card {{
    background:white; border:1px solid #e2e8f0; border-radius:12px;
    padding:16px 20px; margin-bottom:14px;
  }}
  .section-title {{
    font-size:10px; font-weight:800; color:#003366; text-transform:uppercase;
    letter-spacing:2px; margin-bottom:12px; padding-bottom:8px;
    border-bottom:2px solid #b1d431; display:flex; align-items:center; gap:6px;
  }}
  .info-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }}
  .info-item label {{ font-size:9px; font-weight:700; color:#94a3b8; text-transform:uppercase; display:block; margin-bottom:3px; }}
  .info-item .val {{ font-size:13px; font-weight:700; color:#003366; }}

  /* ══ Tables ══ */
  .data-card {{
    background:white; border:1px solid #e2e8f0; border-radius:12px;
    padding:16px 20px; margin-bottom:14px;
  }}
  table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  thead {{ background:#003366; color:white; }}
  th {{ padding:8px 12px; text-align:left; font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:1px; }}
  td {{ padding:8px 12px; border-bottom:1px solid #f1f5f9; }}
  tbody tr:hover {{ background:#f8fafc; }}
  tbody tr:last-child td {{ border-bottom:none; }}
  .val-normal {{ color:#16a34a; font-weight:700; }}
  .val-warn {{ color:#d97706; font-weight:700; }}
  .val-danger {{ color:#dc2626; font-weight:700; }}

  /* ══ AI panel ══ */
  .ai-panel {{
    background:white; border:1px solid #e0e7ff; border-radius:12px;
    padding:20px; margin-bottom:14px; display:none;
  }}
  .ai-content {{ font-size:13px; line-height:1.9; color:#1e293b; white-space:pre-wrap; }}

  /* ══ Images ══ */
  .images-card {{
    background:white; border:1px solid #e2e8f0; border-radius:12px;
    padding:16px 20px; margin-bottom:14px;
  }}
  .img-wrap {{ background:#0f172a; border-radius:10px; overflow:hidden; padding:6px; margin-bottom:12px; text-align:center; }}
  .img-wrap img {{ max-width:100%; opacity:0; transition:opacity 0.3s; border-radius:6px; }}

  /* ══ Action buttons ══ */
  .action-bar {{
    display:flex; gap:10px; margin-bottom:16px; flex-wrap:wrap;
  }}
  .btn-ai {{
    background:#4f46e5; color:white; border:none; padding:10px 20px;
    border-radius:10px; font-weight:700; font-size:12px; cursor:pointer;
    font-family:'Noto Sans Georgian',sans-serif;
  }}
  .btn-print {{
    background:#b1d431; color:#003366; border:none; padding:10px 20px;
    border-radius:10px; font-weight:700; font-size:12px; cursor:pointer;
    font-family:'Noto Sans Georgian',sans-serif;
  }}

  /* ══ Signature ══ */
  .signature-block {{
    background:white; border:1px solid #e2e8f0; border-radius:12px;
    padding:16px 20px; margin-bottom:14px;
    display:grid; grid-template-columns:1fr 1fr; gap:20px;
  }}
  .sig-field {{ border-bottom:1px solid #334155; padding-bottom:4px; height:32px; }}
  .sig-label {{ font-size:10px; color:#94a3b8; margin-top:6px; }}

  /* ══ Footer ══ */
  .report-footer {{
    text-align:center; font-size:10px; color:#94a3b8; padding:12px;
    border-top:1px solid #e2e8f0; margin-top:8px;
  }}

  @media print {{
    body {{ background:white; }}
    .page {{ padding:0; }}
    .action-bar {{ display:none; }}
    .ai-panel {{ display:block !important; }}
  }}
</style>
</head>
<body>
<div class="page">

  <!-- Clinic Header -->
  <div class="clinic-header">
    <div>
      <div class="clinic-name">INNOVA <span>PACS</span></div>
      <div class="clinic-sub">სამედიცინო ცენტრი ინოვა · სხეულის შემადგენლობის კვლევა</div>
    </div>
    <div style="text-align:right;font-size:11px;color:#93c5fd;">
      <div>კვლევის თარიღი</div>
      <div style="font-size:16px;font-weight:800;color:white;">{study_date}</div>
      <div style="margin-top:4px;">აპარატი: PRIMUS (OSTEOSYS)</div>
    </div>
  </div>

  <!-- Report Title -->
  <div class="report-title">
    <h1>სხეულის შემადგენლობის ანალიზისა და მეტაბოლური პროფილის დასკვნა</h1>
    <p>DEXA / Total Body Composition Summary</p>
  </div>

  <!-- Action buttons -->
  <div class="action-bar">
    <button class="btn-ai" id="ai-btn" onclick="generateConclusion()">🤖 AI დასკვნის გენერაცია</button>
    <button class="btn-print" onclick="window.print()">🖨 ბეჭდვა</button>
  </div>

  <!-- Patient Info -->
  <div class="patient-card">
    <div class="section-title">👤 პაციენტის ინფორმაცია</div>
    <div class="info-grid">
      <div class="info-item">
        <label>სახელი, გვარი</label>
        <div class="val">{p_name}</div>
      </div>
      <div class="info-item">
        <label>პაციენტის ID</label>
        <div class="val">{p_id}</div>
      </div>
      <div class="info-item">
        <label>დაბადების თარიღი</label>
        <div class="val">{p_dob}</div>
      </div>
      <div class="info-item">
        <label>ასაკი</label>
        <div class="val">{p_age}</div>
      </div>
      <div class="info-item">
        <label>სქესი / ეთნოსი</label>
        <div class="val">{sex_label} / ევროპული</div>
      </div>
      <div class="info-item">
        <label>კვლევის № (Accession)</label>
        <div class="val">{accession}</div>
      </div>
    </div>
  </div>

  <!-- DEXA სურათები -->
  <div class="images-card">
    <div class="section-title">🖼 დენსიტომეტრიული სურათები</div>
    {images_html if images_html else '<p style="color:#94a3b8;text-align:center;padding:20px;">სურათები ვერ მოიძებნა</p>'}
  </div>

  <!-- AI დასკვნა panel -->
  <div class="ai-panel" id="ai-panel">
    <div class="section-title">🤖 AI დასკვნა — Gemini Vision</div>
    <div id="ai-loading" style="text-align:center;padding:30px;color:#94a3b8;">
      <div style="font-size:28px;margin-bottom:8px;">⏳</div>
      <div>სურათი მუშავდება Gemini AI-ით...</div>
    </div>
    <div id="ai-content" class="ai-content" style="display:none;"></div>
    <div id="ai-error" style="display:none;color:#ef4444;padding:12px;background:#fef2f2;border-radius:8px;"></div>
  </div>

  <!-- Signature -->
  <div class="signature-block">
    <div>
      <div class="sig-field"></div>
      <div class="sig-label">კვლევის შემსრულებელი ექიმი (ხელმოწერა / ბეჭედი)</div>
    </div>
    <div>
      <div class="sig-field" style="padding-top:4px;color:#003366;font-weight:700;">
        {study_date}
      </div>
      <div class="sig-label">თარიღი</div>
    </div>
  </div>

  <!-- Footer -->
  <div class="report-footer">
    Innova Medical Center · DEXA/BMD Body Composition Report · Study UID: {study_uid[:40]}
  </div>

</div>

<script>
async function generateConclusion() {{
    const btn     = document.getElementById('ai-btn');
    const panel   = document.getElementById('ai-panel');
    const loading = document.getElementById('ai-loading');
    const content = document.getElementById('ai-content');
    const errDiv  = document.getElementById('ai-error');

    btn.disabled  = true;
    btn.innerText = '⏳ მუშავდება...';
    panel.style.display  = 'block';
    loading.style.display = 'block';
    content.style.display = 'none';
    errDiv.style.display  = 'none';
    panel.scrollIntoView({{behavior:'smooth'}});

    try {{
        const r    = await fetch('/doctor/dexa-analyze/{study_uid}', {{credentials:'include'}});
        const data = await r.json();
        loading.style.display = 'none';
        if (data.conclusion) {{
            content.style.display = 'block';
            content.innerText = data.conclusion;
            btn.innerText = '✅ დასკვნა მზადაა';
        }} else {{
            errDiv.style.display = 'block';
            errDiv.innerText = '❌ ' + (data.error || 'შეცდომა');
            btn.disabled = false;
            btn.innerText = '🤖 AI დასკვნის გენერაცია';
        }}
    }} catch(e) {{
        loading.style.display = 'none';
        errDiv.style.display  = 'block';
        errDiv.innerText = '❌ ' + e.message;
        btn.disabled = false;
        btn.innerText = '🤖 AI დასკვნის გენერაცია';
    }}
}}
</script>
</body>
</html>"""

    return HTMLResponse(content=html)


# ==========================================================
# სტატისტიკა — /doctor/stats
# ==========================================================
@app.get("/doctor/stats", response_class=HTMLResponse)
async def doctor_stats(doctor_token: str = Cookie(None), d_from: str = "", d_to: str = ""):
    if not verify_doctor_token(doctor_token):
        return RedirectResponse(url="/login")

    # სტატისტიკა პირდაპირ DB-დან
    import re as _re
    try:
        def _clean_date(d):
            """მხოლოდ YYYYMMDD ფორმატის ციფრებს უშვებს, ყველაფერი დანარჩენი (მათ შორის SQL-კოდი) იგდება"""
            d = (d or "").replace("-", "").strip()
            return d if _re.fullmatch(r"\d{8}", d) else None

        d_from_c = _clean_date(d_from)
        d_to_c   = _clean_date(d_to)

        date_filter = ""
        date_params = []
        if d_from_c and d_to_c:
            date_filter  = "AND study_date BETWEEN %s AND %s"
            date_params  = [d_from_c, d_to_c]
        elif d_from_c:
            date_filter  = "AND study_date >= %s"
            date_params  = [d_from_c]

        if not DB_PASS:
            raise RuntimeError("DB_PASS არ არის დაყენებული (.env/environment) — სტატისტიკის endpoint გათიშულია")

        import psycopg2 as _pg
        conn = _pg.connect(host="db", port=5432, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
        cur  = conn.cursor()

        def db_query(sql, params=None):
            cur.execute(sql, params or [])
            return cur.fetchall()

        # სულ კვლევები და პაციენტები
        rows = db_query(f"SELECT COUNT(*), COUNT(DISTINCT patient_fk) FROM study WHERE 1=1 {date_filter};", date_params)
        total_studies  = int(rows[0][0]) if rows else 0
        total_patients = int(rows[0][1]) if rows else 0

        # მოდალობები
        mod_rows = db_query(f"""
            SELECT se.modality, COUNT(DISTINCT st.pk)
            FROM study st
            JOIN series se ON se.study_fk = st.pk
            WHERE se.modality IS NOT NULL {date_filter.replace('study_date', 'st.study_date')}
            GROUP BY se.modality
            ORDER BY COUNT(DISTINCT st.pk) DESC;
        """, date_params)
        modality_count = {}
        for mods, cnt in mod_rows:
            if mods:
                for m in str(mods).split("\\"):
                    m = m.strip()
                    if m:
                        modality_count[m] = modality_count.get(m,0) + int(cnt)

        # თვეების მიხედვით
        month_rows = db_query(f"""
            SELECT SUBSTRING(study_date, 1, 6), COUNT(*)
            FROM study
            WHERE study_date IS NOT NULL AND study_date != '' {date_filter}
            GROUP BY SUBSTRING(study_date, 1, 6)
            ORDER BY 1;
        """, date_params)
        monthly_count = {}
        for ym, cnt in month_rows:
            ym = str(ym).strip()
            if len(ym) == 6:
                monthly_count[f"{{ym[:4]}}-{{ym[4:]}}"] = int(cnt)

        cur.close(); conn.close()
        studies = []
    except Exception as e:
        import traceback; traceback.print_exc(); print(f"DB stats error: {e}")
        total_studies = total_patients = 0
        modality_count = {}
        monthly_count = {}
        studies = []


    # სორტირება
    modality_rows = sorted(modality_count.items(), key=lambda x: -x[1])
    monthly_rows  = sorted(monthly_count.items())[-12:]  # ბოლო 12 თვე

    # Chart data
    import json as _json
    chart_labels = _json.dumps([m for m, _ in monthly_rows])
    chart_data   = _json.dumps([c for _, c in monthly_rows])
    mod_labels   = _json.dumps([m for m, _ in modality_rows[:8]])
    mod_data     = _json.dumps([c for _, c in modality_rows[:8]])

    # modality ცხრილი
    mod_table = ""
    colors = {"CT":"#3b82f6","MR":"#8b5cf6","US":"#10b981","RF":"#f59e0b",
              "CR":"#ef4444","DX":"#06b6d4","BMD":"#b1d431","ES":"#f97316",
              "XA":"#ec4899","XC":"#6366f1"}
    for mod, cnt in modality_rows:
        pct = round(cnt / total_studies * 100, 1) if total_studies else 0
        color = colors.get(mod, "#94a3b8")
        mod_table += f"""
        <tr>
            <td style="padding:10px 14px;">
                <span style="background:{color};color:white;padding:3px 10px;
                      border-radius:8px;font-size:11px;font-weight:800;">{mod}</span>
            </td>
            <td style="padding:10px 14px;font-weight:800;color:#003366;">{cnt}</td>
            <td style="padding:10px 14px;">
                <div style="background:#f1f5f9;border-radius:20px;height:8px;width:100%;">
                    <div style="background:{color};width:{pct}%;height:8px;border-radius:20px;"></div>
                </div>
            </td>
            <td style="padding:10px 14px;color:#64748b;font-size:12px;">{pct}%</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ka">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>სტატისტიკა — Innova PACS</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap');
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Inter',sans-serif; background:#f8fafc; color:#1e293b; }}
  .page {{ max-width:1200px; margin:0 auto; padding:24px; }}
  .header {{ background:#003366; color:white; padding:20px 28px; border-radius:16px;
             display:flex; justify-content:space-between; align-items:center; margin-bottom:24px; }}
  .header h1 {{ font-size:20px; font-weight:900; font-style:italic; }}
  .header h1 span {{ color:#b1d431; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:24px; }}
  .kpi {{ background:white; border-radius:14px; padding:20px 24px;
          border:1px solid #e2e8f0; text-align:center; }}
  .kpi .num {{ font-size:42px; font-weight:900; color:#003366; line-height:1; }}
  .kpi .lbl {{ font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase;
               letter-spacing:2px; margin-top:6px; }}
  .kpi .icon {{ font-size:28px; margin-bottom:8px; }}
  .charts-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px; }}
  .card {{ background:white; border-radius:14px; padding:20px 24px; border:1px solid #e2e8f0; }}
  .card-title {{ font-size:11px; font-weight:800; color:#003366; text-transform:uppercase;
                 letter-spacing:2px; margin-bottom:16px; padding-bottom:10px;
                 border-bottom:2px solid #b1d431; }}
  table {{ width:100%; border-collapse:collapse; }}
  thead {{ background:#f8fafc; }}
  th {{ padding:10px 14px; text-align:left; font-size:10px; font-weight:800;
        color:#94a3b8; text-transform:uppercase; letter-spacing:1px; }}
  tbody tr:hover {{ background:#f9fdf2; }}
  .back-btn {{ background:#003366; color:white; border:none; padding:10px 20px;
               border-radius:10px; font-weight:700; font-size:12px; cursor:pointer;
               text-decoration:none; display:inline-block; }}
</style>
</head>
<body>
<div class="page">

  <div class="header">
    <div>
      <h1>INNOVA <span>PACS</span></h1>
      <div style="font-size:11px;color:#93c5fd;margin-top:3px;">სტატისტიკური ანგარიში</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center;">
      <form method="get" action="/doctor/stats" style="display:flex;gap:8px;align-items:center;">
        <input type="date" id="stats_d_from" name="d_from" value="{d_from}" placeholder="დდ.თთ.წწწწ"
               style="padding:8px 12px;border-radius:8px;border:none;font-size:12px;font-weight:700;color:#003366;">
        <span style="color:#93c5fd;font-weight:700;">—</span>
        <input type="date" id="stats_d_to" name="d_to" value="{d_to}" placeholder="დდ.თთ.წწწწ"
               style="padding:8px 12px;border-radius:8px;border:none;font-size:12px;font-weight:700;color:#003366;">
        <button type="submit" style="background:#b1d431;color:#003366;border:none;padding:8px 16px;
                border-radius:8px;font-weight:800;font-size:12px;cursor:pointer;">ძებნა</button>
      </form>
      <script>
      flatpickr("#stats_d_from", {{ dateFormat: "Y-m-d", altInput: true, altFormat: "d.m.Y", allowInput: true }});
      flatpickr("#stats_d_to",   {{ dateFormat: "Y-m-d", altInput: true, altFormat: "d.m.Y", allowInput: true }});
      </script>
      <a href="/doctor/worklist" class="back-btn">← Worklist</a>
    </div>
  </div>

  <!-- KPI cards -->
  <div class="kpi-grid">
    <div class="kpi">
      <div class="icon">🗂</div>
      <div class="num">{total_studies:,}</div>
      <div class="lbl">სულ კვლევა</div>
    </div>
    <div class="kpi">
      <div class="icon">👤</div>
      <div class="num">{total_patients:,}</div>
      <div class="lbl">უნიკალური პაციენტი</div>
    </div>
    <div class="kpi">
      <div class="icon">📋</div>
      <div class="num">{len(modality_count)}</div>
      <div class="lbl">მოდალობის სახეობა</div>
    </div>
  </div>

  <!-- Charts -->
  <div class="charts-grid">
    <div class="card">
      <div class="card-title">📅 თვეების მიხედვით (ბოლო 12 თვე)</div>
      <canvas id="monthChart" height="200"></canvas>
    </div>
    <div class="card">
      <div class="card-title">🩺 მოდალობების განაწილება</div>
      <canvas id="modChart" height="200"></canvas>
    </div>
  </div>

  <!-- Modality table -->
  <div class="card">
    <div class="card-title">📊 მოდალობების დეტალური სტატისტიკა</div>
    <table>
      <thead><tr>
        <th>მოდალობა</th>
        <th>კვლევა</th>
        <th style="width:40%">გრაფიკი</th>
        <th>%</th>
      </tr></thead>
      <tbody>{mod_table}</tbody>
    </table>
  </div>

</div>

<script>
// თვეების chart
new Chart(document.getElementById('monthChart'), {{
  type: 'bar',
  data: {{
    labels: {chart_labels},
    datasets: [{{
      label: 'კვლევა',
      data: {chart_data},
      backgroundColor: '#003366',
      borderRadius: 6,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ beginAtZero: true }} }}
  }}
}});

// მოდალობების chart
new Chart(document.getElementById('modChart'), {{
  type: 'doughnut',
  data: {{
    labels: {mod_labels},
    datasets: [{{
      data: {mod_data},
      backgroundColor: ['#3b82f6','#8b5cf6','#10b981','#f59e0b','#ef4444','#06b6d4','#b1d431','#f97316'],
      borderWidth: 2,
      borderColor: '#ffffff'
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ position: 'right' }} }}
  }}
}});
</script>
</body>
</html>"""

    return HTMLResponse(content=html)