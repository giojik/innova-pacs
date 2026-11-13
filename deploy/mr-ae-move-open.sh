#!/bin/bash
# ღია (SCHEDULED, დღეს და მომავალი) MR ორდერების გადატანა MR_INNOVA → AWP20611182 — Siemens-ზე რომ გამოჩნდეს
# ჯერ აჩვენებს სიას და დამატებით დადასტურებას ითხოვს. ორდერი არ იშლება — იგივე ჩანაწერი ახლდება.
set -e
cd /opt/pacs/25022026
grep -q '"MR", "AWP20611182"' pacs-portal/onec_order_sync_final.py \
  || echo "⚠️  სინქი ჯერ ისევ MR_INNOVA-ზე წერს — გაუშვით: pacs-deploy mr-ae-siemens"
cat > /tmp/mr_move.py <<'__PY__'
import sys, datetime, requests
from urllib.parse import quote
OLD, NEW = "MR_INNOVA", "AWP20611182"
B = "http://pacs-arc:8080/dcm4chee-arc/aets/RISINNOVA/rs"
apply = len(sys.argv) > 1 and sys.argv[1] == "--apply"
today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=4))).strftime("%Y%m%d")
r = requests.get(f"{B}/mwlitems", params={"includefield": "all", "00400100.00400001": OLD,
                 "00400100.00400002": today + "-"}, headers={"Accept": "application/json"}, timeout=20)
items = r.json() if r.status_code == 200 and r.content else []
def v(d, t, x=""): return ((d.get(t) or {}).get("Value") or [x])[0]
rows = []
for it in items:
    sps = v(it, "00400100", {})
    if v(sps, "00400020", "SCHEDULED") != "SCHEDULED" or v(sps, "00400001") != OLD:
        continue
    rows.append(it)
    print(f"  {v(it,'00080050'):<16} {v(sps,'00400002')} {v(sps,'00400003')[:6]}  {(v(it,'00100010',{}) or {}).get('Alphabetic','')}")
print(f"სულ: {len(rows)}")
if not apply:
    sys.exit(0)
ok = 0
for it in rows:
    sps = it["00400100"]["Value"][0]
    sps["00400001"] = {"vr": "AE", "Value": [NEW]}
    if "00400010" in sps:
        sps["00400010"] = {"vr": "SH", "Value": [NEW]}
    p = requests.post(f"{B}/mwlitems", json=[it],
                      headers={"Content-Type": "application/dicom+json", "Accept": "application/json"}, timeout=20)
    acc = v(it, "00080050")
    if p.status_code in (200, 201, 204):
        ok += 1; print(f"  ✔ {acc} → {NEW}")
    else:
        print(f"  ✗ {acc}: {p.status_code} {p.text[:200]}")
print(f"გადატანილია {ok}/{len(rows)}")
__PY__
docker cp /tmp/mr_move.py pacs-onec-sync:/tmp/mr_move.py
echo "── ღია MR ორდერები $OLD-ზე ──" 2>/dev/null || true
docker exec pacs-onec-sync python3 /tmp/mr_move.py
read -r -p "გადავიტანო ეს ორდერები AWP20611182-ზე? [y/N] " a
[ "$a" = "y" ] || { echo "გაუქმდა"; exit 0; }
docker exec pacs-onec-sync python3 /tmp/mr_move.py --apply
