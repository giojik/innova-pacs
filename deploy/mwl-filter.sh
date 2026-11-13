#!/bin/bash
# worklist-ის ფილტრი აპარატის მიხედვით (dcm4chee Attribute Coercion, WORKLIST AE):
#   US_INNOVA1 → მხოლოდ US · AWP20611182 → მხოლოდ MR · TM_CT_CMW_V3.00 → მხოლოდ CT
# LDAP ბექაპი: /opt/pacs/backup-ldap-<დრო>.ldif. თუ ფილტრი ტესტს ვერ გაივლის — ავტომატურად უქმდება.
set -e
cd /opt/pacs/25022026
RULES="US_INNOVA1:US AWP20611182:MR TM_CT_CMW_V3.00:CT"
BASE="dicomAETitle=WORKLIST,dicomDeviceName=dcm4chee-arc,cn=Devices,cn=DICOM Configuration,dc=dcm4che,dc=org"
TS=$(date +%F-%H%M)
docker exec pacs-ldap slapcat -o ldif-wrap=no > /opt/pacs/backup-ldap-$TS.ldif
echo "LDAP ბექაპი: /opt/pacs/backup-ldap-$TS.ldif ($(wc -l < /opt/pacs/backup-ldap-$TS.ldif) ხაზი)"

ldap() { docker exec -i pacs-ldap sh -c "$1 -x -H ldap://localhost:389 -D cn=admin,\$LDAP_BASE_DN -w \"\$LDAP_ROOTPASS\" $2"; }
reload() { docker exec pacs-qc python3 -c "import requests;r=requests.post('http://pacs-arc:8080/dcm4chee-arc/ctrl/reload',timeout=60);print('reload',r.status_code)"; sleep 2; }
add_rule() {  # $1=AE $2=modality $3=merge-syntax(A|B)
  local key; [ "$3" = A ] && key="00400100.00080060" || key="ScheduledProcedureStepSequence.Modality"
  ldap ldapdelete "\"cn=MWLFilter-$1,$BASE\"" >/dev/null 2>&1 || true
  printf 'dn: cn=MWLFilter-%s,%s\nobjectClass: dcmArchiveAttributeCoercion2\ncn: MWLFilter-%s\ndcmDIMSE: C_FIND_RQ\ndicomTransferRole: SCU\ndcmProperty: SendingApplicationEntityTitle=%s\ndcmURI: merge-attrs:\ndcmMergeAttribute: %s=%s\n\n' \
    "$1" "$BASE" "$1" "$1" "$key" "$2" | ldap ldapadd ""
}
del_rule() { ldap ldapdelete "\"cn=MWLFilter-$1,$BASE\"" >/dev/null 2>&1 || true; }
test_rule() {  # $1=calling AE $2=expected modality → 0 თუ მხოლოდ ეს მოდალობა ბრუნდება
  docker exec -i pacs-onec-sync python3 - "$1" "$2" <<'__PY__'
import sys, datetime
from collections import Counter
from pynetdicom import AE
from pynetdicom.sop_class import ModalityWorklistInformationFind as MWL
from pydicom.dataset import Dataset
aet, want = sys.argv[1], sys.argv[2]
d1 = datetime.date.today(); d0 = d1 - datetime.timedelta(days=7)
q = Dataset(); q.PatientName = ''; q.PatientID = ''
s = Dataset(); s.ScheduledProcedureStepStartDate = f"{d0:%Y%m%d}-{d1:%Y%m%d}"; s.Modality = ''; s.ScheduledStationAETitle = ''
q.ScheduledProcedureStepSequence = [s]
ae = AE(ae_title=aet); ae.add_requested_context(MWL)
a = ae.associate('pacs-arc', 11112, ae_title='WORKLIST')
c = Counter(r.ScheduledProcedureStepSequence[0].Modality for st, r in a.send_c_find(q, MWL) if r)
a.release()
print(f"   {aet}: {dict(c)}")
sys.exit(0 if set(c) <= {want} else 1)
__PY__
}

echo "── ფილტრამდე (ექოს სახელით) ──"; test_rule US_INNOVA1 US || true
OK=""
for SYN in A B; do
  echo "── ცდა: სინტაქსი $SYN ──"
  add_rule US_INNOVA1 US $SYN && reload
  if test_rule US_INNOVA1 US; then OK=$SYN; break; fi
  del_rule US_INNOVA1
done
if [ -z "$OK" ]; then
  reload; echo "✗ ფილტრი ვერ ამუშავდა — ცვლილება გაუქმდა, სერვერი უცვლელია. ეს შედეგი გამომიგზავნეთ."; exit 1
fi
echo "✓ US ფილტრი მუშაობს (სინტაქსი $OK). დანარჩენი აპარატები:"
for R in $RULES; do AE=${R%%:*}; MOD=${R##*:}; [ "$AE" = US_INNOVA1 ] && continue; add_rule "$AE" "$MOD" "$OK"; done
reload
FAIL=0
for R in $RULES; do test_rule "${R%%:*}" "${R##*:}" || FAIL=1; done
echo "── სხვა AE (ფილტრის გარეშე უნდა დარჩეს) ──"; test_rule MWLCHECK ANY || true
[ $FAIL = 0 ] && echo "✓ მზადაა. აპარატებზე worklist განაახლეთ." || echo "⚠️ რომელიმე აპარატზე ფილტრი არ მუშაობს — შედეგი გამომიგზავნეთ"
echo "გაუქმება: pacs-deploy mwl-filter-remove"
