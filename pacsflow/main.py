"""
PacsFlow — PACS Solutions Platform
pacsflow.innovamedical.ge

სტრუქტურა:
  GET  /                  → Landing page
  GET  /login             → Vendor login
  POST /login             → Auth
  GET  /dashboard         → Vendor dashboard (კლინიკების სია)
  GET  /dashboard/new     → ახალი კლინიკის ფორმა
  POST /dashboard/new     → კლინიკის შექმნა + key გენერაცია
  GET  /dashboard/{id}    → კლინიკის დეტალები
  POST /dashboard/{id}/renew   → ვადის გაგრძელება
  POST /dashboard/{id}/revoke  → გაუქმება
  GET  /portal/{slug}     → Client portal (კლინიკა ხედავს)
  GET  /api/heartbeat     → PACS სისტემებიდან ping
"""

import os, json, hmac, hashlib, base64, datetime, sqlite3, uuid, secrets, requests as _req
from fastapi import FastAPI, Form, Request, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

app = FastAPI()

# ── კონფიგი ────────────────────────────────────────────────
SECRET_KEY    = os.getenv("PACSFLOW_SECRET",   "CHANGE_THIS_SECRET_IN_PRODUCTION")
SESSION_TTL   = 28800
DB_PATH       = os.path.join(os.path.dirname(__file__), "pacsflow.db")

# Keycloak SSO
KC_BASE       = os.getenv("KC_BASE",      "http://keycloak:8080")
KC_REALM      = os.getenv("KC_REALM",     "dcm4che")
KC_CLIENT_ID  = os.getenv("KC_CLIENT_ID", "risinnova-ui")
KC_ADMIN_ROLE = os.getenv("KC_ADMIN_ROLE", "admin")
KC_TOKEN_URL  = f"{KC_BASE}/realms/{KC_REALM}/protocol/openid-connect/token"

# ── ფერები ──────────────────────────────────────────────────
NX_DARK  = "#1e40af"
NX_BLUE  = "#0ea5e9"
NX_CYAN  = "#06b6d4"
NX_GLOW  = "#38bdf8"

# ══════════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS clinics (
            id          TEXT PRIMARY KEY,
            slug        TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL,
            contact     TEXT,
            email       TEXT,
            phone       TEXT,
            address     TEXT,
            domain      TEXT,
            ae_limit    INTEGER DEFAULT 5,
            plan        TEXT DEFAULT 'standard',
            modules     TEXT DEFAULT '[]',
            license_key TEXT,
            issued      TEXT,
            expires     TEXT,
            status      TEXT DEFAULT 'active',
            notes       TEXT,
            created_at  TEXT,
            last_seen   TEXT
        );
        CREATE TABLE IF NOT EXISTS invoices (
            id          TEXT PRIMARY KEY,
            clinic_id   TEXT,
            amount      REAL,
            currency    TEXT DEFAULT 'GEL',
            description TEXT,
            issued_at   TEXT,
            due_at      TEXT,
            paid        INTEGER DEFAULT 0,
            paid_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS heartbeats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id   TEXT,
            domain      TEXT,
            ts          TEXT,
            status      TEXT
        );
    """)
    db.commit()
    db.close()

init_db()

# ══════════════════════════════════════════════════════════════
# License helpers
# ══════════════════════════════════════════════════════════════
ALL_MODULES = {
    "worklist_base":    "საბაზისო Worklist",
    "mwl_registration": "MWL რეგისტრაცია",
    "mwl_viewer":       "MWL Viewer",
    "patient_portal":   "პაციენტის პორტალი",
    "share_email":      "Email გაზიარება",
    "zip_download":     "ZIP გადმოწერა",
    "multi_language":   "მრავალენოვნება",
}

PLANS = {
    "starter":    {"label": "Starter",    "price": 150,  "modules": ["worklist_base", "zip_download"]},
    "standard":   {"label": "Standard",   "price": 300,  "modules": ["worklist_base", "zip_download", "share_email", "patient_portal", "multi_language"]},
    "pro":        {"label": "Pro",        "price": 500,  "modules": list(ALL_MODULES.keys())},
    "enterprise": {"label": "Enterprise", "price": 0,    "modules": list(ALL_MODULES.keys())},
}

def generate_license_key(clinic: dict, expires: str) -> str:
    payload = {
        "clinic":   clinic["name"],
        "domain":   clinic["domain"] or "*",
        "modules":  json.loads(clinic["modules"]) if isinstance(clinic["modules"], str) else clinic["modules"],
        "ae_limit": clinic["ae_limit"],
        "expires":  expires,
        "issued":   datetime.date.today().isoformat(),
        "id":       clinic["id"],
    }
    data_str = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    sig      = hmac.new(SECRET_KEY.encode(), data_str.encode(), hashlib.sha256).hexdigest()
    key_b64  = base64.urlsafe_b64encode(data_str.encode()).decode()
    return f"{key_b64}.{sig}"

def days_left(expires: str) -> int | None:
    try:
        return (datetime.date.fromisoformat(expires) - datetime.date.today()).days
    except:
        return None

# ══════════════════════════════════════════════════════════════
# Auth -- Keycloak SSO (doctor_token cookie)
# ══════════════════════════════════════════════════════════════
def _jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.b64decode(part + "==").decode())
    except:
        return {}

def check_session(request: Request) -> bool:
    token = request.cookies.get("doctor_token", "")
    if not token: return False
    try:
        exp = _jwt_payload(token).get("exp", 0)
        return not exp or datetime.datetime.utcnow().timestamp() <= exp
    except: return False

def is_admin_user(request: Request) -> bool:
    if not check_session(request): return False
    p     = _jwt_payload(request.cookies.get("doctor_token", ""))
    roles = (p.get("realm_access", {}).get("roles", []) +
             p.get("resource_access", {}).get(KC_CLIENT_ID, {}).get("roles", []))
    return any(r in roles for r in [KC_ADMIN_ROLE, "nexrad-admin", "super-admin"])

def get_username(request: Request) -> str:
    p = _jwt_payload(request.cookies.get("doctor_token", ""))
    return p.get("name") or p.get("preferred_username", "")

# ══════════════════════════════════════════════════════════════
# CSS / Shared Style
# ══════════════════════════════════════════════════════════════
STYLE = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:    #f8fafc;
  --blue:  {NX_BLUE};
  --cyan:  {NX_CYAN};
  --glow:  {NX_GLOW};
  --card:  #ffffff;
  --border:#e2e8f0;
  --text:  #0f172a;
  --muted: #64748b;
}}
*,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'DM Sans',sans-serif; background:#f8fafc; color:#0f172a;
        min-height:100vh; overflow-x:hidden; }}
h1,h2,h3 {{ font-family:'Syne',sans-serif; }}

/* Nav */
nav {{ display:flex; justify-content:space-between; align-items:center;
       padding:1.2rem 3rem; border-bottom:1px solid #e2e8f0;
       position:sticky; top:0; z-index:100; background:rgba(255,255,255,0.95); box-shadow:0 1px 20px rgba(30,64,175,0.08);
       backdrop-filter:blur(12px); }}
.logo {{ font-family:'Syne',sans-serif; font-weight:800; font-size:1.4rem;
         color:#1e40af; text-decoration:none; letter-spacing:-0.5px; }}
.logo span {{ color:#3b82f6; }}
.nav-link {{ color:#64748b; text-decoration:none; font-size:13px;
             font-weight:500; transition:color 0.2s; }}
.nav-link:hover {{ color:var(--text); }}

/* Buttons */
.btn {{ display:inline-flex; align-items:center; gap:6px; padding:9px 20px;
        border-radius:10px; font-size:12px; font-weight:700; text-decoration:none;
        border:none; cursor:pointer; transition:all 0.2s; text-transform:uppercase;
        letter-spacing:0.5px; font-family:'DM Sans',sans-serif; }}
.btn-primary {{ background:#1e40af; color:white; }}
.btn-primary:hover {{ background:#3b82f6; transform:translateY(-1px);
                       box-shadow:0 8px 25px rgba(30,64,175,0.2); }}
.btn-ghost {{ background:transparent; color:#1e40af; border:1px solid #bfdbfe; }}
.btn-ghost:hover {{ border-color:#1e40af; color:var(--blue); }}
.btn-danger {{ background:#ef4444; color:white; }}
.btn-success {{ background:#10b981; color:white; }}
.btn-sm {{ padding:6px 14px; font-size:11px; }}

/* Cards */
.card {{ background:#ffffff; border:1px solid #e2e8f0; border-radius:16px;
         padding:1.5rem; }}
.card-title {{ font-family:'Syne',sans-serif; font-size:12px; font-weight:700;
               color:var(--muted); text-transform:uppercase; letter-spacing:2px;
               margin-bottom:12px; }}

/* Badges */
.badge {{ display:inline-flex; align-items:center; padding:3px 10px; border-radius:20px;
          font-size:11px; font-weight:700; }}
.badge-active  {{ background:rgba(16,185,129,0.15); color:#10b981; border:1px solid rgba(16,185,129,0.3); }}
.badge-expired {{ background:rgba(239,68,68,0.15);  color:#ef4444; border:1px solid rgba(239,68,68,0.3); }}
.badge-warning {{ background:rgba(245,158,11,0.15); color:#f59e0b; border:1px solid rgba(245,158,11,0.3); }}
.badge-plan    {{ background:rgba(14,165,233,0.15);  color:var(--blue); border:1px solid rgba(14,165,233,0.3); }}

/* Table */
.table-wrap {{ overflow-x:auto; border-radius:12px; border:1px solid var(--border); }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
thead th {{ background:#f1f5f9; color:#64748b; padding:12px 16px; text-align:left;
            font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:1.5px; }}
tbody td {{ padding:14px 16px; border-bottom:1px solid #e2e8f0; }}
tbody tr:last-child td {{ border-bottom:none; }}
tbody tr:hover {{ background:#eff6ff; }}

/* Form */
.form-field {{ margin-bottom:18px; }}
.form-label {{ display:block; font-size:10px; font-weight:700; color:var(--muted);
               text-transform:uppercase; letter-spacing:1.5px; margin-bottom:7px; }}
.form-input {{ width:100%; background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px;
               padding:11px 14px; color:#0f172a; font-size:13px; outline:none;
               transition:border-color 0.2s; font-family:'DM Sans',sans-serif; }}
.form-input:focus {{ border-color:#1e40af; }}
select.form-input option {{ background:#ffffff; }}

/* Layout */
.page {{ max-width:1200px; margin:0 auto; padding:2rem 2rem; }}
.grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
.grid-3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }}
.grid-4 {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }}
@media(max-width:768px) {{ .grid-2,.grid-3,.grid-4 {{ grid-template-columns:1fr; }} }}

/* Stat box */
.stat-box {{ background:#ffffff; border:1px solid #e2e8f0; border-radius:14px;
             padding:1.2rem 1.4rem; }}
.stat-val {{ font-family:'Syne',sans-serif; font-size:2rem; font-weight:800; color:#0f172a; }}
.stat-lbl {{ font-size:11px; color:var(--muted); margin-top:4px; text-transform:uppercase;
             letter-spacing:1px; }}

/* Alert */
.alert {{ padding:12px 16px; border-radius:10px; font-size:13px; font-weight:500;
          margin-bottom:16px; }}
.alert-ok  {{ background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.3); color:#10b981; }}
.alert-err {{ background:rgba(239,68,68,0.1);  border:1px solid rgba(239,68,68,0.3);  color:#ef4444; }}

/* Key box */
.key-box {{ background:#f0f9ff; border:1px solid #bfdbfe; border-radius:10px;
            padding:14px; font-family:monospace; font-size:11px; color:#1d4ed8;
            word-break:break-all; line-height:1.6; }}

/* Glow effect */
.glow-dot {{ width:8px; height:8px; border-radius:50%; background:#0ea5e9;
             box-shadow:0 0 6px rgba(14,165,233,0.4); display:inline-block; }}
</style>
"""

# ══════════════════════════════════════════════════════════════
# 1. Landing Page
# ══════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(f"""<!DOCTYPE html><html lang="ka"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PacsFlow — Medical Imaging Solutions</title>
{STYLE}
<style>
.hero {{ min-height:92vh; display:flex; flex-direction:column; justify-content:center;
         padding:4rem 3rem; position:relative; overflow:hidden; }}
.hero::before {{ content:''; position:absolute; inset:0;
  background:radial-gradient(ellipse 80% 60% at 50% -10%, rgba(30,64,175,0.06) 0%, transparent 60%);
  pointer-events:none; }}
.hero-eyebrow {{ font-size:11px; font-weight:700; color:var(--cyan); text-transform:uppercase;
  letter-spacing:3px; margin-bottom:20px; display:flex; align-items:center; gap:8px; }}
.hero-title {{ font-family:'Syne',sans-serif; font-size:clamp(2.8rem,6vw,5rem);
  font-weight:800; line-height:1.05; color:#0f172a; max-width:700px; margin-bottom:24px; }}
.hero-title .accent {{ color:#1e40af; }}
.hero-sub {{ font-size:1.1rem; color:#64748b; max-width:520px; line-height:1.7;
  margin-bottom:40px; }}
.hero-cta {{ display:flex; gap:12px; flex-wrap:wrap; }}
.feature-icon {{ width:44px; height:44px; border-radius:12px;
  background:rgba(14,165,233,0.1); border:1px solid rgba(14,165,233,0.2);
  display:flex; align-items:center; justify-content:center; font-size:20px;
  margin-bottom:14px; }}
.features {{ padding:5rem 3rem; background:#f8fafc; }}
.features-title {{ font-family:'Syne',sans-serif; font-size:2.2rem; font-weight:800;
  color:#0f172a; text-align:center; margin-bottom:60px; }}
.pricing-section {{ padding:5rem 3rem; background:#eff6ff;
  border-top:1px solid var(--border); border-bottom:1px solid #e2e8f0; }}
.price-card {{ background:#ffffff; border:1px solid #e2e8f0; border-radius:20px;
  padding:2rem; transition:transform 0.2s, border-color 0.2s; }}
.price-card:hover {{ transform:translateY(-4px); border-color:#1e40af; }}
.price-card.featured {{ border-color:#1e40af;
  box-shadow:0 0 40px rgba(30,64,175,0.1); }}
.price-amount {{ font-family:'Syne',sans-serif; font-size:2.5rem; font-weight:800;
  color:#0f172a; margin:12px 0 4px; }}
.cta-section {{ padding:5rem 3rem; text-align:center; }}
footer {{ padding:2rem 3rem; border-top:1px solid #e2e8f0;
  display:flex; justify-content:space-between; align-items:center; }}
</style>
</head><body>

<nav>
  <a href="/" class="logo">Pacs<span>Flow</span></a>
  <div style="display:flex;gap:32px;align-items:center;">
    <a href="#features" class="nav-link">შესაძლებლობები</a>
    <a href="#pricing" class="nav-link">ფასები</a>
    <a href="#contact" class="nav-link">კონტაქტი</a>
    <a href="/login" class="btn btn-primary btn-sm">Dashboard →</a>
  </div>
</nav>

<!-- HERO -->
<section class="hero">
  <div style="max-width:1100px;margin:0 auto;width:100%;">
    <div class="hero-eyebrow">
      <span class="glow-dot"></span>
      სამედიცინო ვიზუალიზაციის პლატფორმა
    </div>
    <h1 class="hero-title">
      PACS სისტემა<br>
      <span class="accent">ახალი სტანდარტი</span><br>
      საქართველოში
    </h1>
    <p class="hero-sub">
      PacsFlow გთავაზობთ DICOM სტანდარტის სრულ PACS გადაწყვეტას —
      CT, MRI, УЗИ, RF სისტემებისთვის. ინსტალაცია, ლიცენზია
      და ტექნიკური მხარდაჭერა ერთ პაკეტში.
    </p>
    <div class="hero-cta">
      <a href="#contact" class="btn btn-primary" style="padding:14px 28px;font-size:13px;">
        დემო მოთხოვნა
      </a>
      <a href="#pricing" class="btn btn-ghost" style="padding:14px 28px;font-size:13px;">
        ფასების ნახვა
      </a>
    </div>
  </div>
</section>

<!-- FEATURES -->
<section class="features" id="features">
  <div style="max-width:1100px;margin:0 auto;">
    <h2 class="features-title" data-ka="რატომ PacsFlow?" data-en="Why PacsFlow?">რატომ PacsFlow?</h2>
    <div class="grid-3" style="gap:24px;">
      <div class="card">
        <div class="feature-icon">🏥</div>
        <h3 style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;color:white;margin-bottom:8px;">სრული PACS გადაწყვეტა</h3>
        <p style="font-size:13px;color:var(--muted);line-height:1.7;">dcm4chee-arc 5.x ბაზაზე — DICOM სტანდარტი, OHIF Viewer, Keycloak ავტენტიფიკაცია.</p>
      </div>
      <div class="card">
        <div class="feature-icon">🔒</div>
        <h3 style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;color:white;margin-bottom:8px;">უსაფრთხოება</h3>
        <p style="font-size:13px;color:var(--muted);line-height:1.7;">SSL, IP ფილტრაცია, ქვეყნების ბლოკირება, Cloudflare ინტეგრაცია.</p>
      </div>
      <div class="card">
        <div class="feature-icon">📋</div>
        <h3 style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;color:white;margin-bottom:8px;">MWL რეგისტრაცია</h3>
        <p style="font-size:13px;color:var(--muted);line-height:1.7;">Modality Worklist — CT/MRI/УЗИ მოწყობილობები ავტომატურად იღებენ პაციენტის მონაცემებს.</p>
      </div>
      <div class="card">
        <div class="feature-icon">📧</div>
        <h3 style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;color:white;margin-bottom:8px;">პაციენტის პორტალი</h3>
        <p style="font-size:13px;color:var(--muted);line-height:1.7;">პაციენტი Email-ით ან QR-ით ხედავს კვლევის შედეგებს. ავტორიზაცია პირადი ნომრით.</p>
      </div>
      <div class="card">
        <div class="feature-icon">📊</div>
        <h3 style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;color:white;margin-bottom:8px;">სტატისტიკა</h3>
        <p style="font-size:13px;color:var(--muted);line-height:1.7;">კვლევების სტატისტიკა მოდალობის მიხედვით, გაზიარების ლოგები, Admin Dashboard.</p>
      </div>
      <div class="card">
        <div class="feature-icon">🔄</div>
        <h3 style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;color:white;margin-bottom:8px;">მიგრაციის სერვისი</h3>
        <p style="font-size:13px;color:var(--muted);line-height:1.7;">ძველი PACS-იდან მონაცემების გადატანა. ღამის მიგრაცია + Live Sync სერვისი.</p>
      </div>
    </div>
  </div>
</section>

<!-- PRICING -->
<section class="pricing-section" id="pricing">
  <div style="max-width:1100px;margin:0 auto;">
    <h2 class="features-title" data-ka="საფასო პაკეტები" data-en="Pricing Plans">საფასო პაკეტები</h2>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:18px;">

      <div class="price-card">
        <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:2px;">Starter</div>
        <div class="price-amount">150<span style="font-size:1rem;color:var(--muted);">₾/თვე</span></div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:20px;">ან 1,500₾/წელი</div>
        <ul style="list-style:none;font-size:13px;color:var(--muted);line-height:2.2;">
          <li>✓ Worklist + OHIF Viewer</li>
          <li>✓ ZIP გადმოწერა</li>
          <li>✓ 3 AE Title</li>
          <li style="color:#374151;">✗ MWL რეგისტრაცია</li>
          <li style="color:#374151;">✗ პაციენტის პორტალი</li>
        </ul>
      </div>

      <div class="price-card featured">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div style="font-size:11px;font-weight:700;color:var(--cyan);text-transform:uppercase;letter-spacing:2px;">Standard</div>
          <span class="badge badge-active" style="font-size:9px;">პოპულარული</span>
        </div>
        <div class="price-amount">300<span style="font-size:1rem;color:var(--muted);">₾/თვე</span></div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:20px;">ან 3,000₾/წელი</div>
        <ul style="list-style:none;font-size:13px;color:var(--muted);line-height:2.2;">
          <li>✓ Worklist + OHIF Viewer</li>
          <li>✓ ZIP + Email გაზიარება</li>
          <li>✓ პაციენტის პორტალი</li>
          <li>✓ KA/EN ენა</li>
          <li>✓ 5 AE Title</li>
        </ul>
      </div>

      <div class="price-card">
        <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:2px;">Pro</div>
        <div class="price-amount">500<span style="font-size:1rem;color:var(--muted);">₾/თვე</span></div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:20px;">ან 5,000₾/წელი</div>
        <ul style="list-style:none;font-size:13px;color:var(--muted);line-height:2.2;">
          <li>✓ ყველა მოდული</li>
          <li>✓ MWL რეგისტრაცია</li>
          <li>✓ MWL Viewer</li>
          <li>✓ 10 AE Title</li>
          <li>✓ მიგრაციის სერვისი</li>
        </ul>
      </div>

      <div class="price-card">
        <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:2px;">Enterprise</div>
        <div class="price-amount" style="font-size:1.8rem;">შეთანხმებით</div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:20px;">წლიური კონტრაქტი</div>
        <ul style="list-style:none;font-size:13px;color:var(--muted);line-height:2.2;">
          <li>✓ ყველა Pro მოდული</li>
          <li>✓ SLA გარანტია</li>
          <li>✓ On-site მხარდაჭერა</li>
          <li>✓ Custom ინტეგრაცია</li>
          <li>✓ ულიმიტო AE Title</li>
        </ul>
      </div>
    </div>
    <p style="text-align:center;font-size:12px;color:var(--muted);margin-top:24px;">
      ყველა პაკეტი მოიცავს ინსტალაციას, Docker კონფიგურაციას, SSL სერტიფიკატს და 30-დღიან უფასო მხარდაჭერას.
    </p>
  </div>
</section>

<!-- CTA / CONTACT -->
<section class="cta-section" id="contact">
  <div style="max-width:600px;margin:0 auto;">
    <h2 style="font-family:'Syne',sans-serif;font-size:2.5rem;font-weight:800;color:#0f172a;margin-bottom:16px;">
      დაგვიკავშირდით
    </h2>
    <p style="color:var(--muted);font-size:1rem;line-height:1.7;margin-bottom:40px;">
      გთხოვთ შეავსოთ ფორმა ან მოგვწეროთ პირდაპირ. 24 საათში დაგიკავშირდებით.
    </p>
    <div class="card" style="text-align:left;">
      <div class="grid-2" style="gap:14px;margin-bottom:14px;">
        <div class="form-field" style="margin:0">
          <label class="form-label">კლინიკის სახელი</label>
          <input class="form-input" placeholder="მაგ: სამედიცინო ცენტრი X">
        </div>
        <div class="form-field" style="margin:0">
          <label class="form-label">საკონტაქტო პირი</label>
          <input class="form-input" placeholder="სახელი გვარი">
        </div>
      </div>
      <div class="form-field">
        <label class="form-label">Email ან ტელეფონი</label>
        <input class="form-input" placeholder="info@clinic.ge ან +995 5XX XXX XXX">
      </div>
      <div class="form-field">
        <label class="form-label">დაინტერესების პაკეტი</label>
        <select class="form-input">
          <option>Starter — 150₾/თვე</option>
          <option>Standard — 300₾/თვე</option>
          <option>Pro — 500₾/თვე</option>
          <option>Enterprise — შეთანხმებით</option>
        </select>
      </div>
      <button class="btn btn-primary" style="width:100%;padding:13px;font-size:13px;justify-content:center;">
        მოთხოვნის გაგზავნა
      </button>
    </div>
    <div style="margin-top:32px;display:flex;gap:32px;justify-content:center;">
      <div style="text-align:center;">
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">EMAIL</div>
        <div style="font-size:13px;color:var(--cyan);">info@innovamedical.ge</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">ტელეფონი</div>
        <div style="font-size:13px;color:var(--cyan);">+995 XXX XXX XXX</div>
      </div>
    </div>
  </div>
</section>

<script>
function setLang(l) {{
  document.querySelectorAll('[data-ka]').forEach(function(el) {{
    var txt = el.getAttribute('data-'+l);
    if (!txt) return;
    if (el.children.length === 0) el.textContent = txt;
    else if (el.tagName === 'A' || el.tagName === 'BUTTON') el.textContent = txt;
  }});
  document.getElementById('btn-ka').style.background = l==='ka' ? 'var(--blue)' : 'transparent';
  document.getElementById('btn-en').style.background = l==='en' ? 'var(--blue)' : 'transparent';
  document.getElementById('btn-ka').style.color = l==='ka' ? 'white' : 'var(--muted)';
  document.getElementById('btn-en').style.color = l==='en' ? 'white' : 'var(--muted)';
  localStorage.setItem('nx_lang', l);
}}
window.addEventListener('DOMContentLoaded', function() {{
  var saved = localStorage.getItem('nx_lang') || 'ka';
  if (saved === 'en') setLang('en');
}});
</script>
<footer>
  <div style="color:var(--muted);font-size:12px;">
    © {datetime.date.today().year} PacsFlow — Medical Imaging Solutions
  </div>
  <div style="display:flex;gap:20px;align-items:center;">
    <a href="/login" class="nav-link">Admin</a>
    <span style="color:var(--border);">|</span>
    <span style="font-size:11px;color:var(--muted);">Powered by Innova Medical</span>
  </div>
</footer>

</body></html>""")


# ══════════════════════════════════════════════════════════════
# 2. Login
# ══════════════════════════════════════════════════════════════
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if check_session(request): return RedirectResponse(url="/dashboard")
    pacs = os.getenv("PACS_LOGIN_URL", "https://pacsflow.innovamedical.ge/login")
    return RedirectResponse(url=pacs, status_code=302)

@app.get("/logout")
async def logout():
    pacs = os.getenv("PACS_LOGOUT_URL", "https://pacsflow.innovamedical.ge/auth/logout")
    resp = RedirectResponse(url=pacs, status_code=302)
    resp.delete_cookie("doctor_token")
    return resp


# ══════════════════════════════════════════════════════════════
# 3. Dashboard — კლინიკების სია
# ══════════════════════════════════════════════════════════════
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not is_admin_user(request):
        return RedirectResponse(url="/login")

    db = get_db()
    clinics = db.execute(
        "SELECT * FROM clinics ORDER BY created_at DESC"
    ).fetchall()
    db.close()

    # სტატისტიკა
    total   = len(clinics)
    active  = sum(1 for c in clinics if c["status"] == "active"
                  and days_left(c["expires"] or "2000-01-01") > 0)
    expiring = sum(1 for c in clinics if c["status"] == "active"
                   and 0 < (days_left(c["expires"] or "2000-01-01") or 0) <= 30)
    expired = sum(1 for c in clinics if (days_left(c["expires"] or "2000-01-01") or -1) < 0)

    rows = ""
    for c in clinics:
        d = days_left(c["expires"] or "2000-01-01")
        if d is None or d < 0:
            badge = '<span class="badge badge-expired">ვადაგასული</span>'
        elif d <= 30:
            badge = f'<span class="badge badge-warning">⏳ {d} დღე</span>'
        else:
            badge = f'<span class="badge badge-active">✓ {d} დღე</span>'

        plan_badge = f'<span class="badge badge-plan">{PLANS.get(c["plan"],{}).get("label","—")}</span>'
        last = c["last_seen"] or "—"
        if last != "—":
            last = last[:16]

        rows += f"""<tr>
          <td>
            <a href="/dashboard/{c['id']}" style="color:#0f172a;font-weight:600;text-decoration:none;font-family:'Syne',sans-serif;">
              {c['name']}
            </a>
            <div style="font-size:11px;color:var(--muted);margin-top:2px;">{c['domain'] or '—'}</div>
          </td>
          <td>{plan_badge}</td>
          <td>{badge}</td>
          <td style="font-size:12px;color:var(--muted);">{c['expires'] or '—'}</td>
          <td>
            <span style="display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--muted);">
              {"<span class='glow-dot' style='width:6px;height:6px;'></span>" if last != "—" else ""}
              {last}
            </span>
          </td>
          <td style="text-align:right;">
            <a href="/dashboard/{c['id']}" class="btn btn-ghost btn-sm">გახსნა</a>
          </td>
        </tr>"""

    return HTMLResponse(f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><title>PacsFlow Dashboard</title>{STYLE}</head><body>

<nav>
  <a href="/dashboard" class="logo">Pacs<span>Flow</span></a>
  <div style="display:flex;gap:16px;align-items:center;">
    <a href="/dashboard/new" class="btn btn-primary btn-sm">+ ახალი კლინიკა</a>
    <a href="/logout" class="nav-link">გამოსვლა</a>
  </div>
</nav>

<div class="page">
  <div style="margin-bottom:28px;">
    <h1 style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:#0f172a;">
      კლინიკები
    </h1>
    <p style="color:var(--muted);font-size:13px;margin-top:4px;">ლიცენზირებული PACS სისტემები</p>
  </div>

  <div class="grid-4" style="margin-bottom:28px;">
    <div class="stat-box">
      <div class="stat-val">{total}</div>
      <div class="stat-lbl">სულ კლინიკა</div>
    </div>
    <div class="stat-box">
      <div class="stat-val" style="color:#10b981;">{active}</div>
      <div class="stat-lbl">აქტიური</div>
    </div>
    <div class="stat-box">
      <div class="stat-val" style="color:#f59e0b;">{expiring}</div>
      <div class="stat-lbl">იწურება (30დ)</div>
    </div>
    <div class="stat-box">
      <div class="stat-val" style="color:#ef4444;">{expired}</div>
      <div class="stat-lbl">ვადაგასული</div>
    </div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>კლინიკა</th>
          <th>პაკეტი</th>
          <th>სტატუსი</th>
          <th>ვადა</th>
          <th>ბოლო Heartbeat</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows if rows else '<tr><td colspan="6" style="text-align:center;padding:40px;color:var(--muted);">კლინიკა ჯერ არ არის დამატებული</td></tr>'}
      </tbody>
    </table>
  </div>
</div>
</body></html>""")


# ══════════════════════════════════════════════════════════════
# 4. ახალი კლინიკა
# ══════════════════════════════════════════════════════════════
@app.get("/dashboard/new", response_class=HTMLResponse)
async def new_clinic_page(request: Request):
    if not is_admin_user(request):
        return RedirectResponse(url="/login")

    plan_opts = "".join([
        f'<option value="{k}">{v["label"]} — {v["price"]}₾/თვე</option>'
        for k, v in PLANS.items()
    ])

    mod_checks = "".join([
        f'''<label style="display:flex;align-items:center;gap:8px;padding:8px 12px;
            background:#111827;border-radius:8px;border:1px solid var(--border);cursor:pointer;">
          <input type="checkbox" name="modules" value="{k}" style="accent-color:var(--blue);">
          <span style="font-size:12px;">{v}</span>
        </label>'''
        for k, v in ALL_MODULES.items()
    ])

    return HTMLResponse(f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><title>ახალი კლინიკა — PacsFlow</title>{STYLE}</head><body>

<nav>
  <a href="/dashboard" class="logo">Pacs<span>Flow</span></a>
  <a href="/dashboard" class="nav-link">← სია</a>
</nav>

<div class="page" style="max-width:700px;">
  <h1 style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;
      color:white;margin-bottom:24px;">ახალი კლინიკა</h1>

  <form method="post" action="/dashboard/new">
    <div class="card" style="margin-bottom:16px;">
      <div class="card-title">კლინიკის ინფო</div>
      <div class="grid-2">
        <div class="form-field">
          <label class="form-label">კლინიკის სახელი *</label>
          <input type="text" name="name" class="form-input" required placeholder="სამედიცინო ცენტრი X">
        </div>
        <div class="form-field">
          <label class="form-label">Slug (URL) *</label>
          <input type="text" name="slug" class="form-input" required placeholder="clinic-x"
            pattern="[a-z0-9-]+" title="მხოლოდ მცირე ლათინური, ციფრები, -">
        </div>
        <div class="form-field">
          <label class="form-label">საკონტაქტო პირი</label>
          <input type="text" name="contact" class="form-input" placeholder="სახელი გვარი">
        </div>
        <div class="form-field">
          <label class="form-label">Email</label>
          <input type="email" name="email" class="form-input" placeholder="info@clinic.ge">
        </div>
        <div class="form-field">
          <label class="form-label">ტელეფონი</label>
          <input type="text" name="phone" class="form-input" placeholder="+995 5XX XXX XXX">
        </div>
        <div class="form-field">
          <label class="form-label">დომეინი (PACS სერვერი)</label>
          <input type="text" name="domain" class="form-input" placeholder="ris.clinic.ge ან * ნებისმიერი">
        </div>
        <div class="form-field" style="grid-column:1/-1;">
          <label class="form-label">მისამართი</label>
          <input type="text" name="address" class="form-input" placeholder="ქ. თბილისი, ул. ...">
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px;">
      <div class="card-title">ლიცენზია</div>
      <div class="grid-2">
        <div class="form-field">
          <label class="form-label">პაკეტი</label>
          <select name="plan" class="form-input" onchange="setPlanModules(this.value)">{plan_opts}</select>
        </div>
        <div class="form-field">
          <label class="form-label">AE Title ლიმიტი</label>
          <input type="number" name="ae_limit" class="form-input" value="5" min="1" max="50">
        </div>
        <div class="form-field">
          <label class="form-label">ლიცენზიის ვადა</label>
          <input type="date" name="expires" class="form-input" required
            value="{(datetime.date.today() + datetime.timedelta(days=365)).isoformat()}">
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px;">
      <div class="card-title">მოდულები</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        {mod_checks}
      </div>
    </div>

    <div class="card" style="margin-bottom:24px;">
      <div class="card-title">შენიშვნა</div>
      <textarea name="notes" class="form-input" rows="3"
        placeholder="შეკვეთის დეტალები, სპეციალური პირობები..."></textarea>
    </div>

    <button type="submit" class="btn btn-primary" style="width:100%;padding:14px;font-size:13px;justify-content:center;">
      კლინიკის შექმნა + License Key გენერაცია
    </button>
  </form>
</div>

<script>
const planModules = {json.dumps({k: v["modules"] for k, v in PLANS.items()})};
function setPlanModules(plan) {{
  const mods = planModules[plan] || [];
  document.querySelectorAll('input[name="modules"]').forEach(cb => {{
    cb.checked = mods.includes(cb.value);
  }});
}}
// default
setPlanModules('standard');
</script>
</body></html>""")

@app.post("/dashboard/new")
async def create_clinic(request: Request):
    if not is_admin_user(request):
        return RedirectResponse(url="/login")

    form    = await request.form()
    modules = form.getlist("modules")
    clinic  = {
        "id":         str(uuid.uuid4()),
        "slug":       form.get("slug","").strip().lower(),
        "name":       form.get("name","").strip(),
        "contact":    form.get("contact",""),
        "email":      form.get("email",""),
        "phone":      form.get("phone",""),
        "address":    form.get("address",""),
        "domain":     form.get("domain","*").strip() or "*",
        "ae_limit":   int(form.get("ae_limit", 5)),
        "plan":       form.get("plan","standard"),
        "modules":    json.dumps(modules),
        "expires":    form.get("expires",""),
        "notes":      form.get("notes",""),
        "status":     "active",
        "created_at": datetime.datetime.now().isoformat(),
        "last_seen":  None,
    }
    key = generate_license_key(clinic, clinic["expires"])
    clinic["license_key"] = key
    clinic["issued"]      = datetime.date.today().isoformat()

    db = get_db()
    db.execute("""
        INSERT INTO clinics
        (id,slug,name,contact,email,phone,address,domain,ae_limit,plan,modules,
         license_key,issued,expires,status,notes,created_at,last_seen)
        VALUES
        (:id,:slug,:name,:contact,:email,:phone,:address,:domain,:ae_limit,:plan,:modules,
         :license_key,:issued,:expires,:status,:notes,:created_at,:last_seen)
    """, clinic)
    db.commit()
    db.close()

    return RedirectResponse(url=f"/dashboard/{clinic['id']}", status_code=303)


# ══════════════════════════════════════════════════════════════
# 5. კლინიკის დეტალები
# ══════════════════════════════════════════════════════════════
@app.get("/dashboard/{clinic_id}", response_class=HTMLResponse)
async def clinic_detail(request: Request, clinic_id: str):
    if not is_admin_user(request):
        return RedirectResponse(url="/login")

    db     = get_db()
    c      = db.execute("SELECT * FROM clinics WHERE id=?", (clinic_id,)).fetchone()
    hbs    = db.execute(
        "SELECT * FROM heartbeats WHERE clinic_id=? ORDER BY ts DESC LIMIT 10",
        (clinic_id,)
    ).fetchall()
    db.close()

    if not c:
        return HTMLResponse("<h2>ვერ მოიძებნა</h2>", status_code=404)

    d       = days_left(c["expires"] or "2000-01-01")
    modules = json.loads(c["modules"]) if c["modules"] else []

    if d is None or d < 0:
        status_badge = '<span class="badge badge-expired">ვადაგასული</span>'
    elif d <= 30:
        status_badge = f'<span class="badge badge-warning">⏳ {d} დღე</span>'
    else:
        status_badge = f'<span class="badge badge-active">✓ {d} დღე</span>'

    mod_list = "".join([
        f'<span class="badge {"badge-active" if m in modules else "badge-expired"}" style="margin:3px;">'
        f'{"✓" if m in modules else "✗"} {ALL_MODULES.get(m, m)}</span>'
        for m in ALL_MODULES
    ])

    hb_rows = "".join([
        f'<tr><td style="font-size:11px;">{h["ts"][:16]}</td>'
        f'<td style="font-size:11px;color:var(--muted);">{h["domain"]}</td>'
        f'<td><span class="badge badge-active" style="font-size:9px;">OK</span></td></tr>'
        for h in hbs
    ]) or '<tr><td colspan="3" style="padding:20px;text-align:center;color:var(--muted);font-size:12px;">Heartbeat ჯერ არ შემოსულა</td></tr>'

    # expiry +1 year
    try:
        new_exp = (datetime.date.fromisoformat(c["expires"]) + datetime.timedelta(days=365)).isoformat()
    except:
        new_exp = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()

    return HTMLResponse(f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><title>{c['name']} — PacsFlow</title>{STYLE}</head><body>

<nav>
  <a href="/dashboard" class="logo">Pacs<span>Flow</span></a>
  <div style="display:flex;gap:12px;align-items:center;">
    <a href="/portal/{c['slug']}" target="_blank" class="btn btn-ghost btn-sm">კლიენტის ხედი ↗</a>
    <a href="/dashboard" class="nav-link">← სია</a>
  </div>
</nav>

<div class="page" style="max-width:900px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px;">
    <div>
      <h1 style="font-family:'Syne',sans-serif;font-size:1.8rem;font-weight:800;color:#0f172a;">
        {c['name']}
      </h1>
      <div style="display:flex;gap:8px;align-items:center;margin-top:8px;">
        {status_badge}
        <span class="badge badge-plan">{PLANS.get(c['plan'],{{}}).get('label','—')}</span>
      </div>
    </div>
    <div style="display:flex;gap:8px;">
      <form method="post" action="/dashboard/{clinic_id}/revoke" style="margin:0;">
        <button type="submit" class="btn btn-danger btn-sm"
          onclick="return confirm('ლიცენზია გაუქმდება. დარწმუნებული ხართ?')">გაუქმება</button>
      </form>
    </div>
  </div>

  <div class="grid-2" style="gap:20px;margin-bottom:20px;">

    <!-- ინფო -->
    <div class="card">
      <div class="card-title">კლინიკის ინფო</div>
      <table style="border:none;">
        <tbody>
          {"".join([f'<tr><td style="color:var(--muted);font-size:11px;padding:5px 0;width:120px;">{k}</td><td style="font-size:13px;padding:5px 0;">{v or "—"}</td></tr>'
            for k,v in [("საკონტაქტო",c['contact']),("Email",c['email']),("ტელეფონი",c['phone']),
                        ("მისამართი",c['address']),("დომეინი",c['domain']),("AE ლიმიტი",c['ae_limit']),
                        ("შექმნილია",c['created_at'][:10] if c['created_at'] else "—")]])}
        </tbody>
      </table>
    </div>

    <!-- ლიცენზია -->
    <div class="card">
      <div class="card-title">ლიცენზია</div>
      <div style="margin-bottom:16px;">
        <div style="font-size:11px;color:var(--muted);margin-bottom:6px;">License Key</div>
        <div class="key-box">{c['license_key'] or '—'}</div>
        <button onclick="navigator.clipboard.writeText('{c['license_key'] or ''}').then(()=>alert('კოპირდა!'))"
          class="btn btn-ghost btn-sm" style="margin-top:8px;">📋 კოპირება</button>
      </div>
      <div style="display:flex;gap:20px;font-size:12px;">
        <div><span style="color:var(--muted);">გაცემა: </span>{c['issued'] or '—'}</div>
        <div><span style="color:var(--muted);">ვადა: </span>{c['expires'] or '—'}</div>
      </div>

      <hr style="border-color:var(--border);margin:16px 0;">

      <form method="post" action="/dashboard/{clinic_id}/renew">
        <div class="card-title">ვადის გაგრძელება</div>
        <div style="display:flex;gap:8px;align-items:flex-end;">
          <div style="flex:1;">
            <label class="form-label">ახალი ვადა</label>
            <input type="date" name="expires" class="form-input" value="{new_exp}">
          </div>
          <button type="submit" class="btn btn-success btn-sm" style="white-space:nowrap;">
            განახლება + Key
          </button>
        </div>
      </form>
    </div>
  </div>

  <!-- მოდულები -->
  <div class="card" style="margin-bottom:20px;">
    <div class="card-title">მოდულები</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;">{mod_list}</div>
  </div>

  <!-- Heartbeat -->
  <div class="card">
    <div class="card-title">Heartbeat ისტორია</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>დრო</th><th>დომეინი</th><th>სტატუსი</th></tr></thead>
        <tbody>{hb_rows}</tbody>
      </table>
    </div>
  </div>

  {"" if not c['notes'] else f'<div class="card" style="margin-top:20px;"><div class="card-title">შენიშვნა</div><p style="font-size:13px;color:var(--muted);line-height:1.7;">{c["notes"]}</p></div>'}
</div>
</body></html>""")


@app.post("/dashboard/{clinic_id}/renew")
async def renew_license(request: Request, clinic_id: str, expires: str = Form(...)):
    if not is_admin_user(request):
        return RedirectResponse(url="/login")

    db = get_db()
    c  = db.execute("SELECT * FROM clinics WHERE id=?", (clinic_id,)).fetchone()
    if not c:
        db.close()
        raise HTTPException(404)

    clinic  = dict(c)
    new_key = generate_license_key(clinic, expires)
    db.execute(
        "UPDATE clinics SET expires=?, license_key=?, issued=?, status='active' WHERE id=?",
        (expires, new_key, datetime.date.today().isoformat(), clinic_id)
    )
    db.commit()
    db.close()
    return RedirectResponse(url=f"/dashboard/{clinic_id}", status_code=303)


@app.post("/dashboard/{clinic_id}/revoke")
async def revoke_license(request: Request, clinic_id: str):
    if not is_admin_user(request):
        return RedirectResponse(url="/login")

    db = get_db()
    db.execute("UPDATE clinics SET status='revoked' WHERE id=?", (clinic_id,))
    db.commit()
    db.close()
    return RedirectResponse(url="/dashboard", status_code=303)


# ══════════════════════════════════════════════════════════════
# 6. Client Portal — კლინიკა ხედავს სტატუსს
# ══════════════════════════════════════════════════════════════
@app.get("/portal/{slug}", response_class=HTMLResponse)
async def client_portal(slug: str):
    db = get_db()
    c  = db.execute("SELECT * FROM clinics WHERE slug=?", (slug,)).fetchone()
    db.close()

    if not c:
        return HTMLResponse("<h2 style='color:#0f172a;padding:2rem;'>კლინიკა ვერ მოიძებნა</h2>", status_code=404)

    d       = days_left(c["expires"] or "2000-01-01")
    modules = json.loads(c["modules"]) if c["modules"] else []

    if d is None or d < 0:
        status_color = "#ef4444"
        status_text  = "ლიცენზიის ვადა გასულია"
        status_icon  = "✗"
    elif d <= 30:
        status_color = "#f59e0b"
        status_text  = f"ლიცენზია იწურება {d} დღეში"
        status_icon  = "⏳"
    else:
        status_color = "#10b981"
        status_text  = f"ლიცენზია აქტიურია ({d} დღე)"
        status_icon  = "✓"

    mod_items = "".join([
        f'<div style="display:flex;align-items:center;gap:10px;padding:10px 14px;'
        f'background:#f8fafc;border-radius:10px;border:1px solid {"rgba(16,185,129,0.2)" if m in modules else "var(--border)"};">'
        f'<span style="color:{"#10b981" if m in modules else "#374151"};font-size:16px;">{"✓" if m in modules else "✗"}</span>'
        f'<span style="font-size:13px;color:{"var(--text)" if m in modules else "var(--muted)"};">{ALL_MODULES[m]}</span>'
        f'</div>'
        for m in ALL_MODULES
    ])

    return HTMLResponse(f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"><title>{c['name']} — PacsFlow Portal</title>{STYLE}</head><body>

<nav>
  <a href="/" class="logo">Pacs<span>Flow</span></a>
  <span style="font-size:12px;color:var(--muted);">კლიენტის პორტალი</span>
</nav>

<div class="page" style="max-width:700px;">
  <div class="card" style="margin-bottom:20px;border-color:{status_color};
      border-top:3px solid {status_color};">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <h1 style="font-family:'Syne',sans-serif;font-size:1.5rem;font-weight:800;
            color:#0f172a;margin-bottom:4px;">{c['name']}</h1>
        <p style="font-size:13px;color:{status_color};font-weight:600;">
          {status_icon} {status_text}
        </p>
      </div>
      <div style="font-family:'Syne',sans-serif;font-size:3rem;font-weight:800;
          color:{status_color};opacity:0.4;">{d if d is not None and d >= 0 else "✗"}</div>
    </div>
  </div>

  <div class="grid-2" style="margin-bottom:20px;">
    <div class="card">
      <div class="card-title">ლიცენზიის დეტალები</div>
      <table><tbody>
        <tr><td style="color:var(--muted);font-size:11px;padding:5px 0;width:100px;">პაკეტი</td>
            <td><span class="badge badge-plan">{PLANS.get(c['plan'],{{}}).get('label','—')}</span></td></tr>
        <tr><td style="color:var(--muted);font-size:11px;padding:5px 0;">გაცემა</td>
            <td style="font-size:13px;">{c['issued'] or '—'}</td></tr>
        <tr><td style="color:var(--muted);font-size:11px;padding:5px 0;">ვადა</td>
            <td style="font-size:13px;color:{status_color};font-weight:600;">{c['expires'] or '—'}</td></tr>
        <tr><td style="color:var(--muted);font-size:11px;padding:5px 0;">AE ლიმიტი</td>
            <td style="font-size:13px;">{c['ae_limit']}</td></tr>
      </tbody></table>
    </div>
    <div class="card">
      <div class="card-title">საკონტაქტო</div>
      <table><tbody>
        <tr><td style="color:var(--muted);font-size:11px;padding:5px 0;width:80px;">კომპანია</td>
            <td style="font-size:13px;">PacsFlow / Innova Medical</td></tr>
        <tr><td style="color:var(--muted);font-size:11px;padding:5px 0;">Email</td>
            <td style="font-size:13px;">info@innovamedical.ge</td></tr>
        <tr><td style="color:var(--muted);font-size:11px;padding:5px 0;">ტელ.</td>
            <td style="font-size:13px;">+995 XXX XXX XXX</td></tr>
      </tbody></table>
      {'<a href="mailto:info@innovamedical.ge?subject=ლიცენზიის%20განახლება%20' + c["name"] + '" class="btn btn-primary btn-sm" style="margin-top:12px;width:100%;justify-content:center;">ვადის გაგრძელება →</a>' if (d is not None and d <= 30) else ''}
    </div>
  </div>

  <div class="card">
    <div class="card-title">ჩართული მოდულები</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px;">
      {mod_items}
    </div>
  </div>
</div>
</body></html>""")


# ══════════════════════════════════════════════════════════════
# 7. Heartbeat API — PACS სისტემებიდან ping
# ══════════════════════════════════════════════════════════════
@app.post("/api/heartbeat")
async def heartbeat(request: Request):
    try:
        body      = await request.json()
        clinic_id = body.get("id") or body.get("clinic")
        domain    = body.get("domain", "")

        db = get_db()
        c  = db.execute(
            "SELECT id FROM clinics WHERE id=? OR slug=? OR domain=?",
            (clinic_id, clinic_id, domain)
        ).fetchone()

        if c:
            now = datetime.datetime.now().isoformat()
            db.execute("UPDATE clinics SET last_seen=? WHERE id=?", (now, c["id"]))
            db.execute(
                "INSERT INTO heartbeats (clinic_id,domain,ts,status) VALUES (?,?,?,'ok')",
                (c["id"], domain, now)
            )
            db.commit()
            db.close()
            return JSONResponse({"status": "ok"})

        db.close()
        return JSONResponse({"status": "unknown"}, status_code=404)
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)
