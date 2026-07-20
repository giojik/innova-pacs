import os, html, datetime, json as _json
from urllib.parse import quote
import requests
import psycopg2
import psycopg2.extras as pg_extras
from fastapi import FastAPI, Cookie, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import jwt as _jwt
from jwt import PyJWKClient as _PyJWKClient

app = FastAPI()

# ==========================================================
# კონფიგურაცია
# ==========================================================
AE_TITLE           = os.getenv("AE_TITLE", "RISINNOVA")
PACS_URL           = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"   # SKIP_AUTH_LOCAL_REQUESTS=true — token არ სჭირდება შიდა ქსელიდან
KEYCLOAK_JWKS_URL  = "http://keycloak:8080/realms/dcm4che/protocol/openid-connect/certs"
URL_PREFIX         = os.getenv("ADMIN_URL_PREFIX", "/pacs-qc").rstrip("/")

DB_PARAMS = {
    "host": "db",
    "database": os.getenv("DB_NAME", "pacsdb"),
    "user": os.getenv("DB_USER", "pacs"),
    "password": os.getenv("DB_PASS", "pacs"),
}

_jwks_client = None


def verify_doctor_token(token: str):
    global _jwks_client
    if not token:
        return None
    try:
        if _jwks_client is None:
            _jwks_client = _PyJWKClient(KEYCLOAK_JWKS_URL)
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return _jwt.decode(token, signing_key.key, algorithms=["RS256"], options={"verify_aud": False})
    except Exception as e:
        print(f"doctor_token verification failed: {e}")
        return None


def get_roles(claims):
    if not claims:
        return []
    return claims.get("realm_access", {}).get("roles", [])


ALLOWED_ROLES = {"admin", "quality_control"}


def has_access(claims):
    return bool(ALLOWED_ROLES.intersection(get_roles(claims)))


def get_client_ip(request: Request) -> str:
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "—"


# ==========================================================
# QC Audit Log — append-only, ცალკე ცხრილი
# ==========================================================
def ensure_qc_audit_table():
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qc_audit_log (
                id BIGSERIAL PRIMARY KEY,
                event_time TIMESTAMPTZ NOT NULL DEFAULT now(),
                performed_by VARCHAR(255),
                action_type VARCHAR(40) NOT NULL,
                patient_id VARCHAR(128),
                patient_name VARCHAR(255),
                study_uid VARCHAR(128),
                reason_code VARCHAR(64),
                old_value JSONB,
                new_value JSONB,
                ip_address VARCHAR(64),
                success BOOLEAN,
                error_message TEXT
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_qc_audit_time ON qc_audit_log(event_time DESC);")
        conn.commit()
        cur.close()
        conn.close()
        print("qc_audit_log ცხრილი მზადაა")
    except Exception as e:
        print(f"qc_audit_log ცხრილის შექმნა ვერ მოხერხდა: {e}")


@app.on_event("startup")
async def _startup():
    ensure_qc_audit_table()


def log_qc_event(performed_by, action_type, patient_id=None, patient_name=None,
                  study_uid=None, reason_code=None, old_value=None, new_value=None,
                  ip_address=None, success=True, error_message=None):
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO qc_audit_log
            (performed_by, action_type, patient_id, patient_name, study_uid, reason_code,
             old_value, new_value, ip_address, success, error_message)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            performed_by, action_type, patient_id, patient_name, study_uid, reason_code,
            pg_extras.Json(old_value) if old_value else None,
            pg_extras.Json(new_value) if new_value else None,
            ip_address, success, error_message,
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"log_qc_event({action_type}) ჩაწერა ვერ მოხერხდა: {e}")


def get_qc_audit_entries(limit=100):
    entries = []
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        cur.execute("""
            SELECT event_time, performed_by, action_type, patient_id, patient_name,
                   study_uid, reason_code, ip_address, success, error_message
            FROM qc_audit_log ORDER BY event_time DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        for r in rows:
            entries.append({
                "time": r[0].strftime("%Y-%m-%d %H:%M:%S") if r[0] else "—",
                "performed_by": r[1] or "—", "action_type": r[2],
                "patient_id": r[3] or "—", "patient_name": r[4] or "—",
                "study_uid": r[5] or "—", "reason_code": r[6] or "—",
                "ip": r[7] or "—", "success": r[8], "error": r[9] or "",
            })
    except Exception as e:
        print(f"qc_audit_log fetch error: {e}")
    return entries


# ==========================================================
# dcm4chee-arc QIDO-RS — პაციენტის/კვლევის ძებნა
# ==========================================================
def _tag_val(item, tag, default=""):
    values = item.get(tag, {}).get("Value") or [default]
    return values[0] if values else default


def search_studies(patient_name="", patient_id=""):
    try:
        params = {"includefield": "all", "limit": 200, "fuzzymatching": "false"}
        if patient_name.strip():
            params["PatientName"] = f"*{patient_name.strip().upper()}*"
        if patient_id.strip():
            params["PatientID"] = patient_id.strip()
        r = requests.get(f"{PACS_URL}/studies", params=params,
                          headers={"Accept": "application/json"}, timeout=15)
        if r.status_code != 200:
            return []
        studies = r.json()
        results = []
        for s in studies:
            name_raw = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "")
            results.append({
                "study_uid": _tag_val(s, "0020000D"),
                "patient_name": name_raw.replace("^", " ").strip(),
                "patient_name_dicom": name_raw,
                "patient_id": _tag_val(s, "00100020"),
                "birth_date": _tag_val(s, "00100030"),
                "sex": _tag_val(s, "00100040"),
                "issuer": _tag_val(s, "00100021"),
                "study_date": _tag_val(s, "00080020"),
                "accession": _tag_val(s, "00080050"),
                "description": _tag_val(s, "00081030"),
                "modality": ", ".join(s.get("00080061", {}).get("Value", []) or s.get("00080060", {}).get("Value", [])),
            })
        results.sort(key=lambda x: x["study_date"], reverse=True)
        return results
    except Exception as e:
        print(f"search_studies error: {e}")
        return []


REJECT_REASONS = [
    ("113001", "ხარისხის მიზეზით"),
    ("113037", "პაციენტის უსაფრთხოების მიზეზით"),
    ("113038", "არასწორი Modality Worklist ჩანაწერი"),
    ("113039", "შენახვის ვადა ამოწურული"),
]


def reject_study(study_uid: str, code_value: str):
    """dcm4chee-arc-ის REST Reject სერვისი — 'soft delete'/quarantine, არა
    დაუყოვნებელი ფიზიკური წაშლა."""
    try:
        url = f"{PACS_URL}/studies/{study_uid}/reject/{code_value}%5EDCM"
        r = requests.post(url, headers={"Accept": "application/json"}, timeout=20)
        if r.status_code in (200, 204):
            return True, None
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, str(e)


def update_patient_info(patient_id: str, name_dicom: str, birth_date: str, sex: str, issuer: str = ""):
    """dcm4chee-arc-ის PUT /patients/{PatientID} REST სერვისი.

    2026-07-20: staging-ზე დადასტურდა, რომ ეს endpoint (study-level
    PUT /studies/{uid}-სგან განსხვავებით) რეალურად მუშაობს — მთლიანად
    გვერდს უვლის StudyMgtRS.java-ს გატეხილ MessageFormat კოდს, რადგან
    სხვა (PatientMgtRS) კოდის ბილიკს იყენებს. ცვლილება ვრცელდება ამ
    პაციენტის ყველა კვლევაზე ერთდროულად (სწორი ქცევაა).

    ერთადერთი შემთხვევა, როცა ეს ვერ იმუშავებს: თუ ამ Patient ID-ზე
    რამდენიმე ცალკეული პაციენტის ჩანაწერია (დუბლირება) — მაშინ
    dcm4chee-arc აბრუნებს NonUniquePatientException-ს (409), და საჭიროა
    dcm4chee-arc-ის UI."""
    try:
        url = f"{PACS_URL}/patients/{patient_id}"
        body = {
            "00100020": {"vr": "LO", "Value": [patient_id]},
            "00100010": {"vr": "PN", "Value": [{"Alphabetic": name_dicom}]},
        }
        if birth_date:
            body["00100030"] = {"vr": "DA", "Value": [birth_date]}
        if sex:
            body["00100040"] = {"vr": "CS", "Value": [sex]}
        if issuer:
            body["00100021"] = {"vr": "LO", "Value": [issuer]}
        r = requests.put(
            url, data=_json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/dicom+json", "Accept": "application/json"}, timeout=20,
        )
        if r.status_code in (200, 204):
            return True, None
        is_duplicate = "Multiple Patients" in r.text or "NonUnique" in r.text
        err_msg = f"HTTP {r.status_code}: {r.text[:300]}"
        if is_duplicate:
            err_msg = "DUPLICATE_PATIENT_ID: " + err_msg
        return False, err_msg
    except Exception as e:
        return False, str(e)


# ==========================================================
# UI — იგივე light-theme CSS, რაც pacs-admin-ში
# ==========================================================
BASE_CSS = """
:root{
  --bg:#EEF2F5; --sidebar:#FFFFFF; --surface:#FFFFFF; --surface-2:#F3F6F8;
  --surface-hover:#E9EEF2; --border:#DCE3E9; --text:#1E2933; --text-muted:#66768A;
  --text-faint:#93A2AD; --accent:#0E8F82; --accent-text:#FFFFFF;
  --success:#1E8E4F; --success-bg:#E6F6EC; --warning:#B7791F; --warning-bg:#FCF1D8;
  --danger:#D0393E; --danger-bg:#FBE9E9; --info:#2F6FE4; --info-bg:#E8EFFD; --radius:8px;
}
*{box-sizing:border-box; margin:0; padding:0;}
body{background:var(--bg); color:var(--text); font-family:'Inter',sans-serif; font-size:14px;}
.app{display:flex; min-height:100vh;}
.sidebar{width:220px; flex-shrink:0; background:var(--sidebar); border-right:1px solid var(--border); padding:20px 0;}
.sidebar__brand{display:flex; align-items:center; gap:10px; padding:0 20px 18px 20px; border-bottom:1px solid var(--border); margin-bottom:10px;}
.sidebar__mark{width:32px; height:32px; border-radius:7px; background:linear-gradient(155deg, var(--danger), #8f1f24); display:flex; align-items:center; justify-content:center; font-weight:700; font-size:13px; color:#fff;}
.sidebar__title{font-weight:600; font-size:14.5px;}
.nav{display:flex; flex-direction:column; gap:2px; padding:0 10px;}
.nav-item{display:block; padding:9px 12px; border-radius:7px; color:var(--text-muted); text-decoration:none; font-size:13.5px; font-weight:500;}
.nav-item:hover{background:var(--surface-hover); color:var(--text);}
.nav-item.active{background:var(--surface-2); color:var(--text); border:1px solid var(--border);}
.main{flex:1; min-width:0;}
.topbar{height:56px; border-bottom:1px solid var(--border); display:flex; align-items:center; padding:0 26px; font-weight:600; font-size:17px;}
.content{padding:24px 26px 50px 26px;}
.panel{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:18px 20px; margin-bottom:16px;}
.panel__title{font-weight:600; font-size:14.5px; margin-bottom:4px;}
.panel__sub{font-size:11.5px; color:var(--text-faint); margin-bottom:12px;}
table{width:100%; border-collapse:collapse;}
thead th{text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--text-faint); font-weight:600; padding:11px 14px; border-bottom:1px solid var(--border); background:var(--surface-2);}
tbody td{padding:11px 14px; border-bottom:1px solid var(--border); font-size:13px;}
tbody tr:last-child td{border-bottom:none;}
tbody tr:hover{background:var(--surface-2);}
.table-wrap{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); overflow:hidden;}
.cell-muted{color:var(--text-muted);}
.cell-mono{font-family:monospace; font-size:12px; color:var(--text-muted);}
.badge{display:inline-flex; align-items:center; gap:5px; padding:3px 9px; border-radius:20px; font-size:11.5px; font-weight:500; white-space:nowrap;}
.badge-success{background:var(--success-bg); color:var(--success);}
.badge-danger{background:var(--danger-bg); color:var(--danger);}
.badge-warning{background:var(--warning-bg); color:var(--warning);}
.badge-neutral{background:var(--surface-2); color:var(--text-muted); border:1px solid var(--border);}
.empty-state{text-align:center; padding:40px 20px; color:var(--text-faint); font-size:13px;}
.notice{font-size:12px; color:var(--text-faint); background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius); padding:10px 14px; margin-bottom:14px;}
.btn{display:inline-flex; align-items:center; gap:6px; padding:8px 14px; border-radius:7px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:var(--surface); color:var(--text); text-decoration:none;}
.btn:hover{background:var(--surface-hover);}
.btn-danger{background:var(--danger); color:#fff; border-color:var(--danger);}
.text-input, .select-input{background:var(--surface); border:1px solid var(--border); color:var(--text); font-size:13px; padding:8px 11px; border-radius:7px; font-family:inherit;}
"""

NAV_ITEMS = [("/", "ძებნა / Reject"), ("/log", "QC აუდიტ ლოგი")]


def render_shell(active_path, page_title, content):
    nav_html = "".join(
        f'<a class="nav-item {"active" if p == active_path else ""}" href="{URL_PREFIX}{p}">{label}</a>'
        for p, label in NAV_ITEMS
    )
    return f"""<!DOCTYPE html>
<html lang="ka"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PACS QC — {html.escape(page_title)}</title>
<style>{BASE_CSS}</style>
</head><body>
<div class="app">
  <aside class="sidebar">
    <div class="sidebar__brand">
      <div class="sidebar__mark">QC</div>
      <div class="sidebar__title">PACS Quality Control</div>
    </div>
    <nav class="nav">{nav_html}</nav>
  </aside>
  <main class="main">
    <div class="topbar">{html.escape(page_title)}</div>
    <div class="content">{content}</div>
  </main>
</div>
</body></html>"""


def render_403():
    return HTMLResponse(render_shell("", "წვდომა აკრძალულია",
        '<div class="notice" style="background:var(--danger-bg); color:var(--danger); border-color:var(--danger);">'
        'წვდომა აკრძალულია — საჭიროა admin ან quality_control როლი.</div>'), status_code=403)


# ==========================================================
# ROUTES
# ==========================================================
@app.get("/internal/require-admin")
async def require_admin_check(doctor_token: str = Cookie(None)):
    """nginx auth_request-ისთვის — /dcm4chee-arc/ui2/-ზე წვდომას მხოლოდ 'admin'
    როლს უშვებს, 'quality_control'-ს კი არა."""
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return JSONResponse({"ok": False}, status_code=401)
    if "admin" not in get_roles(claims):
        return JSONResponse({"ok": False}, status_code=403)
    return JSONResponse({"ok": True}, status_code=200)


@app.get("/", response_class=HTMLResponse)
async def search_page(pname: str = "", pid: str = "", ok: str = "", err: str = "", doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims):
        return render_403()

    result_banner = ""
    if ok == "1":
        result_banner = '<div class="notice" style="background:var(--success-bg); color:var(--success); border-color:var(--success);">✓ წარმატებით შესრულდა.</div>'
    elif err:
        result_banner = f'<div class="notice" style="background:var(--danger-bg); color:var(--danger); border-color:var(--danger);">✗ ჩავარდა: {html.escape(err)}</div>'

    results = search_studies(pname, pid) if (pname or pid) else []

    rows = "".join(
        f'<tr><td>{html.escape(s["patient_name"])}</td><td class="cell-mono">{html.escape(s["patient_id"])}</td>'
        f'<td class="cell-mono">{html.escape(s["birth_date"])}</td><td class="cell-mono">{html.escape(s["study_date"])}</td>'
        f'<td>{html.escape(s["modality"])}</td><td class="cell-muted">{html.escape(s["description"])}</td>'
        f'<td>'
        f'<div style="display:flex; flex-direction:column; gap:6px;">'
        f'<a class="btn" style="padding:5px 10px; font-size:12px; text-align:center;" '
        f'href="{URL_PREFIX}/edit-patient?patient_id={html.escape(s["patient_id"])}'
        f'&name={html.escape(s["patient_name_dicom"])}&birth_date={html.escape(s["birth_date"])}&sex={html.escape(s["sex"])}&issuer={html.escape(s["issuer"])}">'
        f'პაციენტის რედაქტირება</a>'
        f'<form method="post" action="{URL_PREFIX}/reject" style="display:flex; gap:6px; margin:0;">'
        f'<input type="hidden" name="study_uid" value="{html.escape(s["study_uid"])}">'
        f'<input type="hidden" name="patient_id" value="{html.escape(s["patient_id"])}">'
        f'<input type="hidden" name="patient_name" value="{html.escape(s["patient_name"])}">'
        f'<select name="reason_code" class="select-input" style="font-size:12px; padding:5px 8px;">'
        + "".join(f'<option value="{code}">{label}</option>' for code, label in REJECT_REASONS) +
        f'</select>'
        f'<button type="submit" class="btn btn-danger" style="padding:5px 10px; font-size:12px;" '
        f'onclick="return confirm(\'დარწმუნებული ხარ, რომ გინდა ამ კვლევის reject? ეს ცხადდება quarantine-ში, სრული ისტორია აუდიტ ლოგში ჩაიწერება.\')">Reject</button>'
        f'</form>'
        f'</div>'
        f'</td></tr>'
        for s in results
    ) or ('<tr><td colspan="7"><div class="empty-state">მოძებნე პაციენტის სახელით ან ID-ით</div></td></tr>' if not (pname or pid)
          else '<tr><td colspan="7"><div class="empty-state">კვლევა ვერ მოიძებნა</div></td></tr>')

    content = f"""
    {result_banner}
    <div class="notice">Reject dcm4chee-arc-ის soft-delete მექანიზმია — კვლევა quarantine-ში გადადის მითითებული მიზეზით, არა დაუყოვნებელი ფიზიკური წაშლა. ყველა მოქმედება იწერება QC აუდიტ ლოგში.</div>
    <div class="panel">
      <div class="panel__title">პაციენტის/კვლევის ძებნა</div>
      <form method="get" action="{URL_PREFIX}/" style="display:flex; gap:8px; margin-bottom:14px;">
        <input type="text" name="pname" class="text-input" placeholder="პაციენტის სახელი" value="{html.escape(pname)}" style="width:240px;">
        <input type="text" name="pid" class="text-input" placeholder="Patient ID" value="{html.escape(pid)}" style="width:180px;">
        <button type="submit" class="btn">ძებნა</button>
      </form>
      <div class="table-wrap"><table>
        <thead><tr><th>პაციენტი</th><th>ID</th><th>დაბ. თარიღი</th><th>კვლევის თარიღი</th><th>მოდალობა</th><th>აღწერა</th><th>მოქმედება</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>
    """
    return render_shell("/", "ძებნა / Reject", content)


@app.post("/reject")
async def reject_endpoint(
    request: Request,
    study_uid: str = Form(...), patient_id: str = Form(""), patient_name: str = Form(""),
    reason_code: str = Form(...), doctor_token: str = Cookie(None),
):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login", status_code=303)
    if not has_access(claims):
        return render_403()

    performed_by = claims.get("preferred_username", "unknown")
    ip = get_client_ip(request)
    reason_label = dict(REJECT_REASONS).get(reason_code, reason_code)

    success, err = reject_study(study_uid, reason_code)
    log_qc_event(
        performed_by=performed_by, action_type="reject",
        patient_id=patient_id, patient_name=patient_name, study_uid=study_uid,
        reason_code=reason_label, ip_address=ip, success=success, error_message=err,
    )
    if success:
        return RedirectResponse(url=f"{URL_PREFIX}/?pid={quote(patient_id)}&ok=1", status_code=303)
    return RedirectResponse(url=f"{URL_PREFIX}/?pid={quote(patient_id)}&err={quote(err or 'უცნობი შეცდომა')}", status_code=303)


@app.get("/edit-patient", response_class=HTMLResponse)
async def edit_patient_form(
    patient_id: str = "", name: str = "", birth_date: str = "", sex: str = "", issuer: str = "",
    doctor_token: str = Cookie(None),
):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims):
        return render_403()

    name_display = name.replace("^", " ").strip()

    # 2026-07-20: დადასტურდა staging-ზე — PUT /patients/{PatientID} (patient-level)
    # ნამდვილად მუშაობს, StudyMgtRS.java-ს გატეხილ კოდის ბილიკს გვერდს უვლის.
    # ცვლილება ვრცელდება ამ პაციენტის ყველა კვლევაზე. თუ პაციენტს დუბლირებული
    # ID აქვს, dcm4chee-arc 409-ს დააბრუნებს — ამ შემთხვევისთვის dcm4chee-arc-ის
    # UI-ის ბმული ისევ ხელმისაწვდომია.
    content = f"""
    <div class="notice">
      ცვლილება ვრცელდება ამ პაციენტის <b>ყველა</b> კვლევაზე. ძველი მნიშვნელობები
      QC აუდიტ ლოგში ინახება. თუ ამ Patient ID-ზე რამდენიმე ცალკეული პაციენტის
      ჩანაწერია (დუბლირება), სისტემა ამას აღმოაჩენს და dcm4chee-arc-ის UI-ს
      შემოგთავაზებთ.
    </div>
    <div class="panel" style="max-width:520px;">
      <div class="panel__title">პაციენტის მონაცემების შესწორება</div>
      <div class="panel__sub">Patient ID: {html.escape(patient_id)}</div>
      <form method="post" action="{URL_PREFIX}/edit-patient">
        <input type="hidden" name="patient_id" value="{html.escape(patient_id)}">
        <input type="hidden" name="old_name" value="{html.escape(name)}">
        <input type="hidden" name="old_birth_date" value="{html.escape(birth_date)}">
        <input type="hidden" name="old_sex" value="{html.escape(sex)}">
        <input type="hidden" name="old_issuer" value="{html.escape(issuer)}">
        <input type="hidden" name="new_issuer" value="{html.escape(issuer)}">
        <div style="margin-bottom:12px;">
          <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">
            სახელი (DICOM ფორმატი: გვარი^სახელი — მაგ. გელაშვილი^თამარ)
          </label>
          <input type="text" name="new_name" class="text-input" style="width:100%;" value="{html.escape(name)}" required>
          <div style="font-size:11px; color:var(--text-faint); margin-top:4px;">ამჟამად: {html.escape(name_display)}</div>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:18px;">
          <div>
            <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">დაბადების თარიღი (YYYYMMDD)</label>
            <input type="text" name="new_birth_date" class="text-input" style="width:100%;" value="{html.escape(birth_date)}" placeholder="19850312">
          </div>
          <div>
            <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">სქესი</label>
            <select name="new_sex" class="select-input" style="width:100%;">
              <option value="M" {"selected" if sex == "M" else ""}>მამრობითი (M)</option>
              <option value="F" {"selected" if sex == "F" else ""}>მდედრობითი (F)</option>
              <option value="O" {"selected" if sex == "O" else ""}>სხვა (O)</option>
            </select>
          </div>
        </div>
        <div style="display:flex; gap:8px; justify-content:flex-end;">
          <a href="{URL_PREFIX}/" class="btn">გაუქმება</a>
          <a href="/dcm4chee-arc/ui2/" target="_blank" class="btn">dcm4chee-arc UI ↗</a>
          <button type="submit" class="btn btn-danger"
            onclick="return confirm('დარწმუნებული ხარ? ეს ცვლილება შეეხება ამ პაციენტის ყველა კვლევას.')">შენახვა</button>
        </div>
      </form>
    </div>
    """
    return render_shell("/", "პაციენტის რედაქტირება", content)


@app.post("/edit-patient")
async def edit_patient_endpoint(
    request: Request,
    patient_id: str = Form(...),
    old_name: str = Form(""), old_birth_date: str = Form(""), old_sex: str = Form(""), old_issuer: str = Form(""),
    new_name: str = Form(...), new_birth_date: str = Form(""), new_sex: str = Form(""), new_issuer: str = Form(""),
    doctor_token: str = Cookie(None),
):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login", status_code=303)
    if not has_access(claims):
        return render_403()

    performed_by = claims.get("preferred_username", "unknown")
    ip = get_client_ip(request)

    success, err = update_patient_info(patient_id, new_name, new_birth_date, new_sex, new_issuer)
    log_qc_event(
        performed_by=performed_by, action_type="update_patient",
        patient_id=patient_id, patient_name=new_name.replace("^", " ").strip(),
        old_value={"name": old_name, "birth_date": old_birth_date, "sex": old_sex, "issuer": old_issuer},
        new_value={"name": new_name, "birth_date": new_birth_date, "sex": new_sex, "issuer": new_issuer},
        ip_address=ip, success=success, error_message=err,
    )
    if success:
        return RedirectResponse(url=f"{URL_PREFIX}/?pid={quote(patient_id)}&ok=1", status_code=303)
    if err and err.startswith("DUPLICATE_PATIENT_ID"):
        msg = "ამ Patient ID-ზე რამდენიმე ცალკეული ჩანაწერია — გამოიყენეთ dcm4chee-arc-ის UI"
        return RedirectResponse(url=f"{URL_PREFIX}/?pid={quote(patient_id)}&err={quote(msg)}", status_code=303)
    return RedirectResponse(url=f"{URL_PREFIX}/?pid={quote(patient_id)}&err={quote(err or 'უცნობი შეცდომა')}", status_code=303)


def qc_result_badge(success, error):
    if success:
        return '<span class="badge badge-success">წარმატებული</span>'
    safe_error = html.escape(error or "")
    return f'<span class="badge badge-danger" title="{safe_error}">ჩავარდნილი</span>'


@app.get("/log", response_class=HTMLResponse)
async def qc_log_page(doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims):
        return render_403()

    entries = get_qc_audit_entries()
    rows = "".join(
        f'<tr><td class="cell-mono">{html.escape(e["time"])}</td><td>{html.escape(e["performed_by"])}</td>'
        f'<td>{html.escape(e["action_type"])}</td><td>{html.escape(e["patient_name"])} ({html.escape(e["patient_id"])})</td>'
        f'<td class="cell-mono">{html.escape(e["study_uid"][:24])}…</td><td>{html.escape(e["reason_code"])}</td>'
        f'<td class="cell-mono">{html.escape(e["ip"])}</td>'
        f'<td>{qc_result_badge(e["success"], e["error"])}</td></tr>'
        for e in entries
    ) or '<tr><td colspan="8"><div class="empty-state">ჩანაწერი ვერ მოიძებნა</div></td></tr>'

    content = f"""
    <div class="panel">
      <div class="panel__title">QC აუდიტ ლოგი</div>
      <div class="panel__sub">append-only · სულ {len(entries)} ჩანაწერი</div>
      <div class="table-wrap"><table>
        <thead><tr><th>დრო</th><th>შემსრულებელი</th><th>მოქმედება</th><th>პაციენტი</th><th>Study UID</th><th>მიზეზი</th><th>IP</th><th>შედეგი</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>
    """
    return render_shell("/log", "QC აუდიტ ლოგი", content)