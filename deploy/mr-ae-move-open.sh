#!/bin/bash
# ღია (SCHEDULED, დღეს და მომავალი) MR ორდერების გადატანა MR_INNOVA → AWP20611182 — Siemens-ზე რომ გამოჩნდეს
# ჯერ აჩვენებს სიას და დამატებით დადასტურებას ითხოვს. ორდერი არ იშლება — იგივე ჩანაწერი ახლდება.
set -e
cd /opt/pacs/25022026
OLD=MR_INNOVA; NEW=AWP20611182
grep -q "\"MR\", \"$NEW\"" pacs-portal/onec_order_sync_final.py \
  || echo "⚠️  სინქი ჯერ ისევ $OLD-ზე წერს — გაუშვით: pacs-deploy mr-ae-siemens"
UIDS=$(docker compose exec -T db psql -U pacs -d pacsdb -At -c "
  SELECT DISTINCT study_iuid FROM mwl_item
  WHERE modality='MR' AND sps_status=0
    AND sps_start_date >= to_char(now() AT TIME ZONE 'Asia/Tbilisi','YYYYMMDD');" 2>/dev/null | tr -d '\r' | tr '\n' ' ')
cat > /tmp/mr_move.py <<'__PY__'
import sys, requests
OLD, NEW = "MR_INNOVA", "AWP20611182"
B = "http://pacs-arc:8080/dcm4chee-arc/aets/RISINNOVA/rs"
W = "http://pacs-arc:8080/dcm4chee-arc/aets/WORKLIST/rs"   # MWL-ის წაკითხვა მხოლოდ WORKLIST AE-ზე
apply = sys.argv[1] == "--apply"
uids = [u for u in sys.argv[2:] if u]
def v(d, t, x=""): return ((d.get(t) or {}).get("Value") or [x])[0]
rows = []
for uid in uids:
    r = requests.get(f"{W}/mwlitems", params={"StudyInstanceUID": uid, "includefield": "all"},
                     headers={"Accept": "application/json"}, timeout=20)
    if r.status_code != 200 or not r.content:
        print(f"  ? {uid[:30]}… GET {r.status_code}"); continue
    for it in r.json():
        sps = v(it, "00400100", {})
        if v(sps, "00400001") == OLD and v(sps, "00400020", "SCHEDULED") == "SCHEDULED":
            rows.append(it)
            print(f"  {v(it,'00080050'):<16} {v(sps,'00400002')} {str(v(sps,'00400003'))[:6]}  {(v(it,'00100010',{}) or {}).get('Alphabetic','')}")
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
docker cp /tmp/mr_move.py pacs-onec-sync:/tmp/mr_move.py >/dev/null
echo "── ღია MR ორდერები $OLD-ზე ──"
docker exec pacs-onec-sync python3 /tmp/mr_move.py --list $UIDS
read -r -p "გადავიტანო ეს ორდერები $NEW-ზე? [y/N] " a
[ "$a" = "y" ] || { echo "გაუქმდა"; exit 0; }
docker exec pacs-onec-sync python3 /tmp/mr_move.py --apply $UIDS
