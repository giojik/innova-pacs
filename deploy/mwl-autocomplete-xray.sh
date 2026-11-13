#!/bin/bash
# ავტომატური დახურვა: CR/DX/DR/RF ერთ „რენტგენის“ ჯგუფად (Shimadzu RF-ით აგზავნის, ორდერი CR-ია → აქამდე არ იხურებოდა)
# ჯერ აჩვენებს რა დაიხურება, ითხოვს დადასტურებას. ბექაპი: pacs-portal/mwl_autocomplete.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
F=pacs-portal/mwl_autocomplete.py
NEW='_GROUPS = [{"CR", "DX", "MG", "DR", "RF"}, {"XA"}]   # რენტგენი ერთ ჯგუფად: Shimadzu (proxy) ყველაფერს RF-ით აგზავნის'
if grep -q '"DR", "RF"}' "$F"; then echo "უკვე გასწორებულია — გამოტოვება"; exit 0; fi
grep -q '^_GROUPS = \[{"CR", "DX", "MG", "DR"}, {"RF", "XA"}\]' "$F" || { echo "✗ _GROUPS ხაზი ვერ მოიძებნა"; exit 1; }
B="$F.bak-$(date +%F-%H%M%S)"; cp "$F" "$B"
python3 - "$F" "$NEW" <<'__PY__'
import sys, re
p, new = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
s = re.sub(r'^_GROUPS = \[\{"CR", "DX", "MG", "DR"\}, \{"RF", "XA"\}\]', new, s, count=1, flags=re.M)
open(p, "w", encoding="utf-8").write(s)
__PY__
python3 -m py_compile "$F" && echo "COMPILE OK"
docker cp "$F" pacs-onec-sync:/tmp/mwl_autocomplete_new.py
echo "── წინასწარი ნახვა (ახალი წესით, ბოლო 7 დღე) ──"
docker exec -w /tmp pacs-onec-sync python3 -c "
import importlib.util
s=importlib.util.spec_from_file_location('m','/tmp/mwl_autocomplete_new.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
m.run(dry=True)" | head -60
read -r -p "ჩავრთო ახალი წესი? [y/N] " a
if [ "$a" != "y" ]; then cp "$B" "$F"; echo "გაუქმდა — ფაილი აღდგენილია"; exit 0; fi
docker compose build pacs-onec-sync && docker compose up -d --no-deps --force-recreate pacs-onec-sync
sleep 20
docker logs --since 1m pacs-onec-sync 2>&1 | grep -E "MWL COMPLETED|MWL status|ჩავარდა" | tail -15 || true
