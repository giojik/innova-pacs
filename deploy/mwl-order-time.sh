#!/bin/bash
# MWL ორდერის დრო: 00:00:00-ის ნაცვლად რეგისტრაციის დრო (თბილისი) → worklist რიგით ლაგდება
# ბექაპი: pacs-portal/onec_order_sync_final.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
echo "── ბოლო 8 ორდერის დრო ახლა (sps_start_time) ──"
docker compose exec -T db psql -U pacs -d pacsdb -P pager=off -c "
SELECT accession_no, sps_start_date, sps_start_time, modality, created_time
FROM mwl_item ORDER BY pk DESC LIMIT 8;" 2>/dev/null
cat > /tmp/order_time_fix.py <<'__FIX__'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
if "_tbilisi_now" in s:
    print("უკვე გასწორებულია — გამოტოვება"); sys.exit(0)
OLD = """    try:
        order_time = datetime.datetime.fromisoformat(order_date_str).time()
    except (ValueError, TypeError):
        order_time = datetime.datetime.now().time()
    return datetime.datetime.combine(planned_date, order_time)
"""
NEW = """    order_time = None
    try:
        order_time = datetime.datetime.fromisoformat(order_date_str).time()
    except (ValueError, TypeError):
        pass
    if order_time is None or order_time == datetime.time(0, 0):
        # 1C-მ საათი არ მოგვცა — ვიყენებთ რეგისტრაციის (სინქის მიერ პირველად ნახვის) დროს,
        # რომ worklist რეგისტრაციის რიგით დალაგდეს და 00:00:00 არ ეწეროს
        order_time = _tbilisi_now().time()
    return datetime.datetime.combine(planned_date, order_time.replace(microsecond=0))


def _tbilisi_now():
    # კონტეინერი შეიძლება UTC-ზე იყოს — საქართველო ყოველთვის UTC+4 (DST არ აქვს)
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=4))).replace(tzinfo=None)
"""
assert s.count(OLD) == 1, "_sched_dt_with_real_time ვერ მოიძებნა"
s = s.replace(OLD, NEW, 1)
s = s.replace("""    except (ValueError, TypeError):
        planned_date = datetime.datetime.now().date()""", """    except (ValueError, TypeError):
        planned_date = _tbilisi_now().date()""", 1)
open(p, "w", encoding="utf-8").write(s); print("OK")
__FIX__
cp pacs-portal/onec_order_sync_final.py pacs-portal/onec_order_sync_final.py.bak-$(date +%F-%H%M)
python3 /tmp/order_time_fix.py pacs-portal/onec_order_sync_final.py
python3 -m py_compile pacs-portal/onec_order_sync_final.py && echo "COMPILE OK"
docker compose build pacs-onec-sync && docker compose up -d --no-deps --force-recreate pacs-onec-sync
echo "✓ ახალი ორდერები რეგისტრაციის დროით ჩაიწერება. შემოწმება ახალი ორდერის შემდეგ:"
echo "  docker compose exec -T db psql -U pacs -d pacsdb -c \"SELECT accession_no, sps_start_time FROM mwl_item ORDER BY pk DESC LIMIT 5;\""
