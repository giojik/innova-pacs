#!/bin/bash
# MR ორდერების Station AE: MR_INNOVA → AWP20611182 (Siemens Magnetom Free.Max), რომ worklist-ში დაინახოს
# ბექაპი: pacs-portal/onec_order_sync_final.py.bak-<დრო>. ეხება მხოლოდ ახალ ორდერებს.
set -e
cd /opt/pacs/25022026
F=pacs-portal/onec_order_sync_final.py
NEW_AE="AWP20611182"
if grep -q "\"MR\", \"$NEW_AE\"" "$F"; then echo "უკვე შეცვლილია — გამოტოვება"; exit 0; fi
N=$(grep -c '"MR", "MR_INNOVA"' "$F" || true)
[ "$N" -ge 1 ] || { echo "✗ \"MR\", \"MR_INNOVA\" ვერ მოიძებნა $F-ში"; exit 1; }
cp "$F" "$F.bak-$(date +%F-%H%M)"
sed -i "s/\"MR\", \"MR_INNOVA\"/\"MR\", \"$NEW_AE\"/g" "$F"
echo "შეიცვალა $N ხაზი:"; grep -n "\"MR\", \"$NEW_AE\"" "$F"
python3 -m py_compile "$F" && echo "COMPILE OK"
docker compose build pacs-onec-sync && docker compose up -d --no-deps --force-recreate pacs-onec-sync
echo "✓ ახალი MR ორდერები $NEW_AE-ზე ჩაიწერება."
