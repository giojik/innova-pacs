#!/bin/bash
# ჩატარებული კვლევის ორდერი → COMPLETED (worklist-იდან ქრება). სინქი ყოველ წუთში ამოწმებს. ორდერი არ იშლება.
# ბექაპი: pacs-portal/onec_order_sync_final.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
cat > pacs-portal/mwl_autocomplete.py <<'__MOD__'
"""
mwl_autocomplete.py — ჩატარებული კვლევის ორდერის დამალვა worklist-იდან.

MWL ჩანაწერს (SCHEDULED) ენიჭება COMPLETED (dcm4chee REST), როცა PACS-ში
მისი კვლევა უკვე შემოვიდა. dcm4chee-ში COMPLETED უკვე იმალება worklist-იდან
(dcmHideSPSWithStatusFromMWL). ჩანაწერი არ იშლება.

შედარება:
  1) ზუსტი  — StudyInstanceUID ან Accession Number ემთხვევა
  2) პაციენტით — იგივე Patient ID + იგივე დღე + იგივე მოდალობის ჯგუფი,
     კვლევა შემოსულია ორდერის შექმნის შემდეგ. თუ იმ დღეს ერთ პაციენტს
     ერთ მოდალობაზე რამდენიმე ორდერი აქვს, იხურება მხოლოდ იმდენი
     (ძველიდან), რამდენი კვლევაც რეალურად შემოვიდა.

    python3 -c "import mwl_autocomplete as m; m.run(dry=True)"   # მხოლოდ ჩვენება
"""
import os, datetime
from collections import defaultdict
import requests
import psycopg2

AE_TITLE = os.getenv("AE_TITLE", "RISINNOVA")
PACS_URL = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"
DB = dict(host=os.getenv("DB_HOST", "db"), database=os.getenv("DB_NAME", "pacsdb"),
          user=os.getenv("DB_USER", "pacs"), password=os.getenv("DB_PASS", "pacs"))
LOOKBACK_DAYS = int(os.getenv("MWL_AUTOCOMPLETE_DAYS", "7"))
MAX_PER_RUN = int(os.getenv("MWL_AUTOCOMPLETE_MAX", "200"))

_GROUPS = [{"CR", "DX", "MG", "DR"}, {"RF", "XA"}]


def mod_group(m):
    m = (m or "").upper()
    for g in _GROUPS:
        if m in g:
            return frozenset(g)
    return frozenset({m})


EXACT_SQL = """
SELECT m.pk, m.study_iuid, m.sps_id, m.accession_no, m.modality, m.sps_start_date, pi.pat_id,
       CASE WHEN EXISTS (SELECT 1 FROM study s WHERE s.study_iuid = m.study_iuid) THEN 'uid' ELSE 'accession' END
FROM mwl_item m LEFT JOIN patient_id pi ON pi.patient_fk = m.patient_fk
WHERE m.sps_status = 0
  AND (EXISTS (SELECT 1 FROM study s WHERE s.study_iuid = m.study_iuid)
       OR (coalesce(m.accession_no,'') <> '' AND EXISTS (SELECT 1 FROM study s WHERE s.accession_no = m.accession_no)))
"""

CAND_SQL = """
SELECT m.pk, m.study_iuid, m.sps_id, m.accession_no, m.modality, m.sps_start_date, pi.pat_id, m.created_time
FROM mwl_item m JOIN patient_id pi ON pi.patient_fk = m.patient_fk
WHERE m.sps_status = 0 AND m.sps_start_date >= %s AND m.sps_start_date <= %s
ORDER BY m.created_time
"""

STUDIES_SQL = """
SELECT s.pk, s.study_date, s.created_time,
       array(SELECT DISTINCT se.modality FROM series se WHERE se.study_fk = s.pk) AS mods
FROM study s JOIN patient_id pi ON pi.patient_fk = s.patient_fk
WHERE pi.pat_id = %s AND s.study_date = %s
  AND NOT EXISTS (SELECT 1 FROM mwl_item m2 WHERE m2.study_iuid = s.study_iuid)
  AND NOT (coalesce(s.accession_no,'') <> '' AND EXISTS (SELECT 1 FROM mwl_item m3 WHERE m3.accession_no = s.accession_no))
"""


def find(conn):
    """აბრუნებს [(pk, study_iuid, sps_id, accession, modality, date, pat_id, how)]."""
    cur = conn.cursor()
    out, seen = [], set()
    cur.execute(EXACT_SQL)
    for r in cur.fetchall():
        out.append(r); seen.add(r[0])

    today = datetime.date.today()
    cur.execute(CAND_SQL, ((today - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
                           today.strftime("%Y%m%d")))
    groups = defaultdict(list)
    for pk, uid, sps, acc, mod, date, pid, created in cur.fetchall():
        if pk in seen or not pid:
            continue
        groups[(pid, date, mod_group(mod))].append((pk, uid, sps, acc, mod, date, pid, created))

    for (pid, date, grp), items in groups.items():
        cur.execute(STUDIES_SQL, (pid, date))
        first_order = min(i[7] for i in items)
        studies = [s for s in cur.fetchall()
                   if s[2] >= first_order - datetime.timedelta(hours=1) and grp.intersection(s[3] or [])]
        for it in items[:len(studies)]:
            out.append(it[:7] + ("patient+date+modality",))
    cur.close()
    return out


def run(dry=False):
    try:
        conn = psycopg2.connect(**DB)
    except Exception as e:
        print(f"[{datetime.datetime.now()}] mwl_autocomplete: DB ვერ დაუკავშირდა: {e}")
        return 0
    try:
        rows = find(conn)
    finally:
        conn.close()
    if dry:
        print(f"DRY-RUN: დაიხურებოდა {len(rows)} ორდერი")
        for pk, uid, sps, acc, mod, date, pid, how in rows:
            print(f"  {date} {mod:<3} {pid:<14} acc={acc:<16} ← {how}")
        return len(rows)

    done = 0
    for pk, uid, sps, acc, mod, date, pid, how in rows[:MAX_PER_RUN]:
        try:
            r = requests.post(f"{PACS_URL}/mwlitems/{uid}/{sps}/status/COMPLETED", timeout=15)
            if r.status_code in (200, 204):
                done += 1
                print(f"[{datetime.datetime.now()}] ✔ MWL COMPLETED: {date} {mod} {pid} acc={acc} ({how})")
            else:
                print(f"[{datetime.datetime.now()}] MWL status {r.status_code}: {pid} acc={acc} — {r.text[:150]}")
        except Exception as e:
            print(f"[{datetime.datetime.now()}] MWL status ჩავარდა {pid} acc={acc}: {e}")
    return done
__MOD__
cat > /tmp/sync_autocomplete.py <<'__FIX__'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
if "mwl_autocomplete" in s:
    print("სინქში უკვე ჩართულია — გამოტოვება"); sys.exit(0)
OLD = "            _process_retry_requests()\n"
assert s.count(OLD) == 1, "loop()-ში _process_retry_requests() ვერ მოიძებნა"
s = s.replace(OLD, OLD + """            try:
                import mwl_autocomplete
                mwl_autocomplete.run()
            except Exception as e:
                print(f"[{datetime.datetime.now()}] mwl_autocomplete ჩავარდა: {e}")
""", 1)
open(p, "w", encoding="utf-8").write(s); print("OK")
__FIX__
cp pacs-portal/onec_order_sync_final.py pacs-portal/onec_order_sync_final.py.bak-$(date +%F-%H%M)
python3 /tmp/sync_autocomplete.py pacs-portal/onec_order_sync_final.py
python3 -m py_compile pacs-portal/onec_order_sync_final.py pacs-portal/mwl_autocomplete.py && echo "COMPILE OK"
docker compose build pacs-onec-sync && docker compose up -d --no-deps --force-recreate pacs-onec-sync
sleep 20
docker logs --since 1m pacs-onec-sync 2>&1 | grep -E "MWL COMPLETED|mwl_autocomplete|MWL status|ჩავარდა" | tail -20 || true
echo "✓ ჩართულია. ლოგი: docker logs -f pacs-onec-sync | grep 'MWL COMPLETED'"
