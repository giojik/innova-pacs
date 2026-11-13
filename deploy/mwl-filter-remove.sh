#!/bin/bash
# worklist-ის ფილტრის (MWLFilter-*) მოხსნა — ყველა აპარატი ისევ ყველა ორდერს დაინახავს
set -e
BASE="dicomAETitle=WORKLIST,dicomDeviceName=dcm4chee-arc,cn=Devices,cn=DICOM Configuration,dc=dcm4che,dc=org"
for AE in US_INNOVA1 AWP20611182 TM_CT_CMW_V3.00; do
  docker exec pacs-ldap sh -c "ldapdelete -x -H ldap://localhost:389 -D cn=admin,\$LDAP_BASE_DN -w \"\$LDAP_ROOTPASS\" \"cn=MWLFilter-$AE,$BASE\"" 2>/dev/null && echo "წაიშალა MWLFilter-$AE" || true
done
docker exec pacs-qc python3 -c "import requests;print('reload',requests.post('http://pacs-arc:8080/dcm4chee-arc/ctrl/reload',timeout=60).status_code)"
