#!/bin/bash
# სინქის დუბლიკატ-შემოწმება: MWL-ის წაკითხვა WORKLIST AE-ით (RISINNOVA 404-ს აბრუნებდა → შემოწმება ჩუმად არ მუშაობდა)
# ბექაპი: pacs-portal/mwl_review.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
echo "── ბოლო 24 სთ-ში ჩავარდნილი შემოწმებები (ცვლილებამდე) ──"
docker logs --since 24h pacs-onec-sync 2>&1 | grep -c "duplicate-check ჩავარდა" || true
cat > /tmp/review_wl_fix.py <<'__FIX__'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
if "MWL_READ_URL" in s:
    print("უკვე გასწორებულია — გამოტოვება"); sys.exit(0)
A = 'PACS_URL = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"\n'
B = '    r = requests.get(f"{PACS_URL}/{path}", params=params,'
assert s.count(A) == 1 and s.count(B) == 1, "mwl_review.py-ში საჭირო ხაზები ვერ მოიძებნა"
s = s.replace(A, A + 'MWL_READ_URL = "http://pacs-arc:8080/dcm4chee-arc/aets/WORKLIST/rs"   # MWL-ის წაკითხვა მხოლოდ WORKLIST AE-ზე\n', 1)
s = s.replace(B, '    r = requests.get(f"{MWL_READ_URL if path == \'mwlitems\' else PACS_URL}/{path}", params=params,', 1)
open(p, "w", encoding="utf-8").write(s); print("OK")
__FIX__
cp pacs-portal/mwl_review.py pacs-portal/mwl_review.py.bak-$(date +%F-%H%M)
python3 /tmp/review_wl_fix.py pacs-portal/mwl_review.py
python3 -m py_compile pacs-portal/mwl_review.py && echo "COMPILE OK"
docker compose build pacs-onec-sync && docker compose up -d --no-deps --force-recreate pacs-onec-sync
echo "✓ შემოწმება ახლა WORKLIST-ით კითხულობს. მომდევნო საათებში: docker logs --since 1h pacs-onec-sync | grep -E 'duplicate-check|🟡'"
