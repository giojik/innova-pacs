#!/bin/bash
# pacs-qc „პაციენტების გაერთიანება“: merge PUT→POST (405-ის გასწორება) + შებრუნებული merge-ის დაცვა
# ბექაპი: pacs-qc/main.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
grep -q "PATIENT MERGE" pacs-qc/main.py || { echo "✗ გაერთიანების გვერდი ჯერ დაყენებული არ არის"; exit 1; }
cat > /tmp/merge_fix.py <<'__FIX__'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
OLD1 = '''                r = requests.put(f"{PACS_URL}/patients/{_mg_qid(pat_id, p['issuer'])}/merge/{_mg_qid(pat_id, surv['issuer'])}",'''
NEW1 = '''                r = requests.post(f"{PACS_URL}/patients/{_mg_qid(pat_id, p['issuer'])}/merge/{_mg_qid(pat_id, surv['issuer'])}",'''
OLD2 = '''            if not err:
                cur.execute("""SELECT (SELECT count(*) FROM study WHERE patient_fk=%(pk)s)'''
NEW2 = '''            if not err:
                cur.execute("SELECT merge_fk FROM patient WHERE pk=%s", (surv_pk,))
                _sr = cur.fetchone()
                if _sr and _sr[0] is not None:
                    err = f"⚠️ merge შებრუნებით შესრულდა: დარჩენილი pk={surv_pk} თვითონ გაერთიანდა pk={_sr[0]}-ში — ხელით შეამოწმეთ, წაშლა არ შესრულდა"
            if not err:
                cur.execute("""SELECT (SELECT count(*) FROM study WHERE patient_fk=%(pk)s)'''
if NEW1 in s:
    print("უკვე გასწორებულია — გამოტოვება"); sys.exit(0)
assert s.count(OLD1) == 1 and s.count(OLD2) == 1, "ბლოკი ვერ მოიძებნა"
s = s.replace(OLD1, NEW1).replace(OLD2, NEW2)
open(p, "w", encoding="utf-8").write(s); print("OK")
__FIX__
cp pacs-qc/main.py pacs-qc/main.py.bak-$(date +%F-%H%M)
python3 /tmp/merge_fix.py pacs-qc/main.py
python3 -m py_compile pacs-qc/main.py && echo "COMPILE OK"
docker compose build pacs-qc && docker compose up -d --no-deps --force-recreate pacs-qc
sleep 4; docker compose exec nginx nginx -s reload 2>/dev/null || true
echo "✓ მზადაა → https://ris.innovamedical.ge/pacs-qc/merge-patients?pid=57001029551"
