#!/bin/bash
# დიაგნოსტიკა (მხოლოდ კითხულობს): Attribute Coercion-ის LDAP სტრუქტურა, WORKLIST AE, worklist label-ები, reload endpoint
cd /opt/pacs/25022026
docker exec pacs-ldap slapcat -o ldif-wrap=no 2>/dev/null > /tmp/ldap.ldif
echo "══ 1. არსებული coercion წესი (ნიმუში) ══"
awk '/^dn: cn=SupplementIssuerOfPatientID,/{p=1} p&&/^$/{exit} p' /tmp/ldap.ldif
echo "══ 2. ყველა coercion წესი (DN + DIMSE + role + AE) ══"
awk '/^dn: /{dn=$0; show=0} /^objectClass: dcmArchiveAttributeCoercion/{show=1} show && /^(dcmDIMSE|dicomTransferRole|dicomAETitle|dcmURI|dcmCoercionSuffix):/{print "   " $0} show && /^objectClass: dcmArchiveAttributeCoercion/{print dn}' /tmp/ldap.ldif | head -60
echo "══ 3. WORKLIST AE entry ══"
awk '/^dn: dicomAETitle=WORKLIST,dicomDeviceName=dcm4chee-arc,/{p=1} p&&/^$/{exit} p' /tmp/ldap.ldif | grep -v -i "^dicomSOPClass\|^dicomTransferSyntax"
echo "══ 4. worklist_label ══"
docker compose exec -T db psql -U pacs -d pacsdb -At -c "SELECT coalesce(worklist_label,'(null)'), count(*) FROM mwl_item GROUP BY 1;" 2>/dev/null
echo "══ 5. LDAP admin env ══"
docker exec pacs-ldap sh -c 'env | grep -E "^LDAP_(ROOTPASS|BASE_DN|ROOTDN)=" | sed -E "s/(PASS=).*/\1***/"'
echo "══ 6. reload endpoint ══"
docker exec pacs-qc python3 -c "
import requests
for u in ('http://pacs-arc:8080/dcm4chee-arc/ctrl/reload',):
    r=requests.options(u,timeout=10); print(r.status_code, r.headers.get('Allow'), u)"
rm -f /tmp/ldap.ldif
