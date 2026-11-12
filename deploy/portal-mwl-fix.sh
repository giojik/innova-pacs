#!/bin/bash
# pacs-portal: ხელით MWL რეგისტრაციის გასწორება (ავტორიზაცია, ქართ. ტრანსლიტერაცია, accession ≤16, თბილისის დრო)
# ბექაპი: pacs-portal/main.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
if grep -q "_ka_translit" pacs-portal/main.py; then echo "უკვე გამოყენებულია — გამოტოვება"; exit 0; fi
cp pacs-portal/main.py pacs-portal/main.py.bak-$(date +%F-%H%M)
cat > /tmp/fix_mwl.py <<'__FIX__'
import re, sys
p = sys.argv[1] if len(sys.argv) > 1 else "main.py"
src = open(p, encoding="utf-8").read()

start = src.index("# 10. პაციენტის ძებნა PACS-ში პირადი ნომრით (MWL lookup)")
start = src.rindex("# ====", 0, start)
end = src.index("# 8. პაციენტის პორტალი")
end = src.rindex("# ====", 0, end)

NEW = r'''# ==========================================================
# MWL helpers — ტრანსლიტერაცია, ვალიდაცია
# ==========================================================
_KA2LAT = {
    "ა":"a","ბ":"b","გ":"g","დ":"d","ე":"e","ვ":"v","ზ":"z","თ":"t","ი":"i",
    "კ":"k","ლ":"l","მ":"m","ნ":"n","ო":"o","პ":"p","ჟ":"zh","რ":"r","ს":"s",
    "ტ":"t","უ":"u","ფ":"p","ქ":"k","ღ":"gh","ყ":"q","შ":"sh","ჩ":"ch","ც":"ts",
    "ძ":"dz","წ":"ts","ჭ":"ch","ხ":"kh","ჯ":"j","ჰ":"h",
}

def _ka_translit(s: str) -> str:
    """ქართული (მხედრული/მთავრული) → ლათინური, ეროვნული სისტემა, აპოსტროფების გარეშე."""
    out = []
    for ch in s or "":
        o = ord(ch)
        if 0x1C90 <= o <= 0x1CBF:          # მთავრული → მხედრული
            ch = chr(o - 0x1C90 + 0x10D0)
        out.append(_KA2LAT.get(ch, ch))
    return "".join(out)

def _dicom_pn(raw: str) -> str:
    """'გვარი სახელი' → 'GVARI^SAKHELI' (ASCII, ^ და \\ გასუფთავებული)."""
    raw = re.sub(r"[\^\\=]", " ", _ka_translit(raw or "")).strip()
    raw = re.sub(r"\s+", " ", raw).upper()
    if not raw:
        return ""
    parts = raw.split(" ", 1)
    return "^".join(parts)[:64]

def _mwl_accession() -> str:
    """სინქის ფორმატი: YYYYMMDD + md5(uuid)[:6] — 14 სიმბოლო (SH ≤ 16)."""
    import hashlib, uuid as _uuid
    return datetime.datetime.now().strftime("%Y%m%d") + hashlib.md5(_uuid.uuid4().hex.encode()).hexdigest()[:6]

def _json_err(code: int, detail: str):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=code, content={"detail": detail})

def _first(ds: dict, tag: str, default=""):
    v = (ds.get(tag) or {}).get("Value") or [default]
    return v[0]

# ==========================================================
# 10. პაციენტის ძებნა PACS-ში პირადი ნომრით (MWL lookup)
# ==========================================================
@app.get("/doctor/mwl/lookup-patient")
def lookup_patient(request: Request, pid: str):
    if not verify_doctor_token(request.cookies.get("doctor_token")):
        return _json_err(401, "Unauthorized")
    pid = (pid or "").strip()
    if not pid:
        return {"found": False}
    try:
        ds = None
        # 1) არქივის კვლევები  2) fallback — არსებული MWL ჩანაწერები
        for path, extra in (("studies", {"StudyDate": "20000101-"}), ("mwlitems", {})):
            r = requests.get(
                f"{PACS_URL}/{path}",
                params={"PatientID": pid, "includefield": "all", "limit": 1,
                        "fuzzymatching": "false", **extra},
                headers={"Accept": "application/json"},
                timeout=8,
            )
            if r.status_code == 200 and r.content and r.json():
                ds = r.json()[0]
                break
        if not ds:
            return {"found": False}

        raw_name = (_first(ds, "00100010", {}) or {}).get("Alphabetic", "")
        dob_raw  = str(_first(ds, "00100030")).strip()
        return {
            "found": True,
            "name":  raw_name.replace("^", " ").strip(),
            "pid":   str(_first(ds, "00100020")).strip(),
            "dob":   f"{dob_raw[:4]}-{dob_raw[4:6]}-{dob_raw[6:8]}" if len(dob_raw) == 8 else "",
            "sex":   str(_first(ds, "00100040")).strip(),
        }
    except Exception as e:
        print(f"❌ lookup-patient error: {e}")
        return _json_err(500, "PACS lookup შეცდომა")

# ==========================================================
# 9. MWL რეგისტრაცია (Modality Worklist — dcm4chee-arc 5.33)
# ==========================================================
@app.post("/doctor/mwl/register")
async def mwl_register(request: Request):
    if not verify_doctor_token(request.cookies.get("doctor_token")):
        return _json_err(401, "Unauthorized")
    try:
        payload = await request.json()
    except Exception:
        return _json_err(400, "Invalid JSON")

    g = lambda k: str(payload.get(k) or "").strip()
    p_id, modality, ae_title = g("p_id"), g("modality").upper(), g("ae_title")
    dicom_name = _dicom_pn(g("p_name"))

    # ── სავალდებულო ველები
    missing = [n for n, v in (("პაციენტის ID", p_id), ("სახელი", dicom_name),
                              ("მოდალობა", modality), ("აპარატი (AE)", ae_title)) if not v]
    if missing:
        return _json_err(400, "შეავსეთ: " + ", ".join(missing))
    if len(p_id) > 64 or len(ae_title) > 16:
        return _json_err(400, "პაციენტის ID ან AE Title ძალიან გრძელია")

    sex = g("p_sex").upper()
    sex = sex if sex in ("M", "F", "O") else ""

    dob = g("p_dob").replace("-", "")
    if not re.fullmatch(r"(19|20)\d{6}", dob):
        dob = ""

    try:
        sched_dt = datetime.datetime.fromisoformat(g("sched_dt"))
    except Exception:
        sched_dt = datetime.datetime.now()

    accession = _mwl_accession()
    study_uid = "2.25." + str(__import__("uuid").uuid4().int)
    referring = _dicom_pn(g("referring"))
    procedure = g("procedure")[:64]

    def el(vr, val):
        return {"vr": vr, "Value": [val]} if val else {"vr": vr}

    mwl_body = {
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": dicom_name}]},
        "00100020": el("LO", p_id),
        "00100030": el("DA", dob),
        "00100040": el("CS", sex),
        "0020000D": el("UI", study_uid),
        "00080050": el("SH", accession),
        "00080090": {"vr": "PN", "Value": [{"Alphabetic": referring}]} if referring else {"vr": "PN"},
        "00321060": el("LO", procedure),
        "00401001": el("SH", accession),
        "00400100": {"vr": "SQ", "Value": [{
            "00400001": el("AE", ae_title),
            "00400002": el("DA", sched_dt.strftime("%Y%m%d")),
            "00400003": el("TM", sched_dt.strftime("%H%M%S")),
            "00080060": el("CS", modality),
            "00400006": {"vr": "PN", "Value": [{"Alphabetic": referring}]} if referring else {"vr": "PN"},
            "00400007": el("LO", procedure),
            "00400009": el("SH", accession),
            "00400010": el("SH", ae_title),
            "00400020": el("CS", "SCHEDULED"),
        }]},
    }

    try:
        r = await run_in_threadpool(
            requests.post,
            f"{PACS_URL}/mwlitems",
            json=[mwl_body],
            headers={"Content-Type": "application/dicom+json", "Accept": "application/json"},
            timeout=10,
        )
    except Exception as e:
        print(f"❌ MWL register exception: {e}")
        return _json_err(502, "PACS-თან კავშირი ვერ მოხერხდა")

    print(f"MWL response: {r.status_code} — {r.text[:300]}")
    if r.status_code in (200, 201, 204):
        print(f"✅ MWL registered: {accession} — {dicom_name} — {ae_title}")
        return {"status": "ok", "accession": accession}
    if r.status_code == 409:
        return _json_err(409, "ამ ID-ით რამდენიმე პაციენტია PACS-ში (409) — გადაამოწმეთ pacs-qc /duplicates")
    return _json_err(502, f"PACS {r.status_code}")

'''
src = src[:start] + NEW + src[end:]

# JS: დრო UTC-ის ნაცვლად ლოკალური (Tbilisi)
old_js = "new Date().toISOString().slice(0,16)"
assert src.count(old_js) == 2, src.count(old_js)
src = src.replace(old_js, "localNowISO()")
src = src.replace("        async function lookupPatient() {{",
"""        function localNowISO() {{
            const d = new Date();
            d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
            return d.toISOString().slice(0,16);
        }}

        async function lookupPatient() {{""", 1)
# JS: შეცდომის ტექსტი textContent-ით (XSS)
old_err = """el.innerHTML = '<div style="background:#fef2f2;color:#991b1b;padding:12px 16px;border-radius:14px;font-weight:700;font-size:12px;margin-top:16px;">❌ ' + (data.detail || 'შეცდომა') + '</div>';"""
assert old_err in src
src = src.replace(old_err, """el.innerHTML = '<div style="background:#fef2f2;color:#991b1b;padding:12px 16px;border-radius:14px;font-weight:700;font-size:12px;margin-top:16px;"></div>';
                    el.firstChild.textContent = '❌ ' + (data.detail || 'შეცდომა');""")

# imports
if "from starlette.concurrency import run_in_threadpool" not in src:
    src = src.replace("from langs import T", "from starlette.concurrency import run_in_threadpool\nfrom langs import T", 1)
    assert "from starlette.concurrency import run_in_threadpool" in src
if not re.search(r"^import re\b", src, re.M):
    src = "import re\n" + src
open(p, "w", encoding="utf-8").write(src)
print("OK")
__FIX__
python3 /tmp/fix_mwl.py pacs-portal/main.py
python3 -m py_compile pacs-portal/main.py && echo "COMPILE OK"
docker compose build pacs-portal && docker compose up -d --no-deps --force-recreate pacs-portal
sleep 4; docker compose exec nginx nginx -s reload 2>/dev/null || true
echo "✓ pacs-portal განახლდა"
