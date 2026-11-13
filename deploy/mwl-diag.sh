#!/bin/bash
# დიაგნოსტიკა (მხოლოდ კითხულობს): MWL სტატუსები, ჩატარებული კვლევების ორდერები worklist-ში, LDAP MWL/MPPS კონფიგი
cd /opt/pacs/25022026
PSQL="docker compose exec -T db psql -U pacs -d pacsdb -P pager=off"
echo "=== 1. mwl_item სვეტები ==="
$PSQL -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='mwl_item' ORDER BY ordinal_position;" 2>/dev/null
echo "=== 2. MWL სტატუსები ==="
$PSQL -c "SELECT sps_status, count(*) FROM mwl_item GROUP BY 1 ORDER BY 1;" 2>/dev/null
echo "=== 3. MWL ჩანაწერები, რომლის კვლევაც უკვე PACS-შია (accession ან StudyUID) ==="
$PSQL -c "
SELECT count(*) AS done_but_listed,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM study s WHERE s.study_iuid = m.study_iuid)) AS by_uid,
       count(*) FILTER (WHERE m.accession_no <> '' AND EXISTS (SELECT 1 FROM study s WHERE s.accession_no = m.accession_no)) AS by_acc
FROM mwl_item m
WHERE EXISTS (SELECT 1 FROM study s WHERE s.study_iuid = m.study_iuid)
   OR (m.accession_no <> '' AND EXISTS (SELECT 1 FROM study s WHERE s.accession_no = m.accession_no));" 2>/dev/null
echo "=== 4. მაგალითი (5) ==="
$PSQL -c "
SELECT m.accession_no, m.sps_status, m.sps_start_date, m.study_iuid = s.study_iuid AS same_uid,
       (SELECT string_agg(DISTINCT se.modality, ',') FROM series se WHERE se.study_fk = s.pk) AS mods
FROM mwl_item m JOIN study s ON s.accession_no = m.accession_no AND m.accession_no <> ''
ORDER BY m.pk DESC LIMIT 5;" 2>/dev/null
echo "=== 5. LDAP: MWL/MPPS პარამეტრები ==="
docker exec pacs-ldap slapcat -o ldif-wrap=no 2>/dev/null \
 | grep -i -E "^dn: dicomAETitle=RISINNOVA,|^dn: dicomDeviceName=dcm4chee-arc,|HideSPS|MWLStatus|SPSStatus|DeleteMWL|MWLIdle|MPPSForward|dcmMWL" | grep -v -i "^dcmMWLAccessionNumberGenerator" | head -40
echo "=== 6. REST: SPS სტატუსის შეცვლის endpoint ==="
S=$($PSQL -At -c "SELECT study_iuid||'/'||sps_id FROM mwl_item ORDER BY pk DESC LIMIT 1;" 2>/dev/null | tr -d '\r')
docker exec pacs-qc python3 -c "
import requests
B='http://pacs-arc:8080/dcm4chee-arc/aets/RISINNOVA/rs/mwlitems/$S'
for p in ('', '/status/COMPLETED'):
    r=requests.options(B+p,timeout=15); print(r.status_code, r.headers.get('Allow'), '| mwlitems/<uid>/<sps>'+p)
"
