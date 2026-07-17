import os, json, csv, datetime, psycopg2, html
from fastapi import FastAPI, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
import jwt as _jwt
from jwt import PyJWKClient as _PyJWKClient

app = FastAPI()

KEYCLOAK_JWKS_URL = "http://keycloak:8080/realms/dcm4che/protocol/openid-connect/certs"
_jwks_client = None

def verify_doctor_token(token: str):
    """იგივე ვერიფიკაცია, რაც pacs-portal-ში — Keycloak-ის JWKS public key-ით
    ვამოწმებთ ხელმოწერასა და ვადას, უბრალო cookie-ს არსებობის ნაცვლად."""
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

# კონფიგურაცია
DB_PARAMS = {
    "host": "db",
    "database": os.getenv("DB_NAME", "pacsdb"),
    "user": os.getenv("DB_USER", "pacs"),
    "password": os.getenv("DB_PASS", "pacs")
}

# Volume-ით გაზიარებული ფაილების გზა
DATA_PATH = "/app/data"
INNOVA_GREEN = "#b1d431"
DARK_BLUE = "#003366"

# --- 1. სტატისტიკის წამოღება ბაზიდან ---
def get_stats(period="month"):
    intervals = {"day": "1 day", "week": "7 days", "month": "30 days", "year": "1 year"}
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        query = f"""
            SELECT series.modality, COUNT(DISTINCT study.pk) 
            FROM study 
            JOIN series ON series.study_fk = study.pk 
            WHERE study.created_time > CURRENT_TIMESTAMP - INTERVAL '{intervals.get(period, "30 days")}'
            GROUP BY series.modality;
        """
        cur.execute(query)
        mod_stats = cur.fetchall()
        
        cur.execute("SELECT COUNT(*) FROM study")
        total_all = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        return mod_stats, total_all
    except:
        return [], 0

# --- 2. გაზიარების ლოგების წაკითხვა CSV-დან ---
def get_recent_logs():
    logs = []
    file_path = f"{DATA_PATH}/share_logs.csv"
    if os.path.exists(file_path):
        try:
            with open(file_path, mode='r', encoding='utf-8-sig') as f:
                reader = list(csv.reader(f))
                # ვიღებთ ბოლო 10 ჩანაწერს (სათაურის გამოკლებით)
                logs = reader[1:][-10:]
                logs.reverse() # უახლესი თავში
        except: pass
    return logs

# --- 3. ადმინ Dashboard ---
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard(period: str = "month", doctor_token: str = Cookie(None)):
    if not verify_doctor_token(doctor_token):
        return RedirectResponse(url="/login")

    mod_stats, total_all = get_stats(period)
    recent_logs = get_recent_logs()
    
    # JSON ფაილების წაკითხვა (რობოტების მონიტორინგი)
    def read_json(filename):
        try:
            with open(f"{DATA_PATH}/{filename}", "r") as f:
                return json.load(f)
        except: return {}

    live_sync = read_json("live_sync_active.json")
    migration = read_json("active_transfers.json")

    # HTML სექციების აწყობა (html.escape — stored XSS-ის თავიდან ასაცილებლად,
    # რადგან ეს მონაცემები CSV/JSON ფაილებიდან მოდის, არა code-იდან)
    stats_html = "".join([f"<div class='bg-slate-50 p-4 rounded-2xl border text-center'><b>{html.escape(str(r[0]))}</b><br><span class='text-xl font-black text-blue-600'>{html.escape(str(r[1]))}</span></div>" for r in mod_stats])

    log_rows = "".join([f"<tr class='border-b'><td class='p-3'>{html.escape(str(l[0]))}</td><td class='p-3'>{html.escape(str(l[1]))}</td><td class='p-3'>{html.escape(str(l[3]))}</td><td class='p-3'>{html.escape(str(l[4]))}</td><td class='p-3 font-bold text-blue-600'>{html.escape(str(l[6]))}</td></tr>" for l in recent_logs])

    return f"""
    <html>
    <head>
        <script src="https://cdn.tailwindcss.com"></script>
        <meta http-equiv="refresh" content="20">
        <title>PACS Admin Command Center</title>
        <style>
            :root {{ --green: {INNOVA_GREEN}; --dark: {DARK_BLUE}; }}
            body {{ font-family: 'Inter', sans-serif; }}
        </style>
    </head>
    <body class="bg-slate-50 p-6">
        <div class="max-w-7xl mx-auto">
            <header class="flex justify-between items-center mb-8">
                <h1 class="text-2xl font-black italic text-[{{DARK_BLUE}}]">INNOVA <span class="text-[{{INNOVA_GREEN}}]">ADMIN HUB</span></h1>
                <div class="flex gap-4">
                    <span class="bg-white px-4 py-2 rounded-xl border text-xs font-bold uppercase tracking-widest text-slate-400">Total Studies: <b class="text-slate-900">{total_all}</b></span>
                    <a href="/doctor/worklist" class="bg-slate-900 text-white px-5 py-2 rounded-xl text-xs font-bold">WORKLIST ⬅️</a>
                </div>
            </header>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <!-- სტატისტიკა -->
                <div class="lg:col-span-2 bg-white p-6 rounded-[35px] shadow-sm border border-slate-100">
                    <div class="flex justify-between items-center mb-6">
                        <h3 class="text-xs font-black uppercase text-slate-400 tracking-widest italic">📊 Modality Statistics</h3>
                        <div class="flex bg-slate-100 p-1 rounded-xl">
                            <a href="?period=day" class="px-4 py-1.5 rounded-lg text-[10px] font-bold { 'bg-white shadow-sm' if period=='day' else 'text-slate-400' }">DAY</a>
                            <a href="?period=week" class="px-4 py-1.5 rounded-lg text-[10px] font-bold { 'bg-white shadow-sm' if period=='week' else 'text-slate-400' }">WEEK</a>
                            <a href="?period=month" class="px-4 py-1.5 rounded-lg text-[10px] font-bold { 'bg-white shadow-sm' if period=='month' else 'text-slate-400' }">MONTH</a>
                        </div>
                    </div>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">{stats_html if stats_html else 'No data for this period'}</div>
                </div>

                <!-- რობოტები -->
                <div class="space-y-6">
                    <div class="bg-white p-6 rounded-[35px] shadow-sm border-t-8 border-blue-500">
                        <h3 class="text-[10px] font-black uppercase text-slate-400 mb-4">⚡ Live Sync ({len(live_sync)})</h3>
                        {"".join([f"<div class='p-2 bg-blue-50 rounded-lg mb-1 text-[11px] font-bold border-l-4 border-blue-400'>{html.escape(str(v['name']))}: {html.escape(str(v['percent']))}%</div>" for k,v in live_sync.items()]) if live_sync else '<p class="text-slate-300 italic text-xs">რიგი ცარიელია</p>'}
                    </div>
                    <div class="bg-white p-6 rounded-[35px] shadow-sm border-t-8 border-orange-500">
                        <h3 class="text-[10px] font-black uppercase text-slate-400 mb-4">🌙 Night Migration ({len(migration)})</h3>
                        {"".join([f"<div class='p-2 bg-orange-50 rounded-lg mb-1 text-[11px] font-bold border-l-4 border-orange-400'>{html.escape(str(v['name']))}: {html.escape(str(v['percent']))}%</div>" for k,v in migration.items()]) if migration else '<p class="text-slate-300 italic text-xs">რობოტი ისვენებს</p>'}
                    </div>
                </div>

                <!-- ბოლო ლოგები -->
                <div class="lg:col-span-3 bg-white p-6 rounded-[35px] shadow-sm border border-slate-100">
                    <h3 class="text-xs font-black uppercase text-slate-400 mb-6 tracking-widest italic">📋 Recent Share Logs</h3>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-[11px]">
                            <thead class="bg-slate-50 text-slate-400 uppercase tracking-wider">
                                <tr><th class="p-3">Time</th><th class="p-3">Doctor</th><th class="p-3">Patient</th><th class="p-3">National ID</th><th class="p-3">Mod</th></tr>
                            </thead>
                            <tbody>{log_rows if log_rows else '<tr><td colspan="5" class="p-10 text-center text-slate-300">No logs found</td></tr>'}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """