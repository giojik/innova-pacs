#!/bin/bash
# pacs-admin: მენიუში ღილაკი „Quality Control ↗“ → /pacs-qc/ (ჩანს admin / quality_control როლებზე)
# ბექაპი: pacs-admin/main.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
cat > /tmp/admin_qc_link.py <<'__FIX__'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
if "QC_LINK" in s:
    print("უკვე დამატებულია — გამოტოვება"); sys.exit(0)
OLD_NAV = '<nav class="nav">{nav_html}</nav>'
OLD_RET = '    return f"""<!DOCTYPE html>\n<html lang="ka"><head><meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>PACS Admin'
assert s.count(OLD_NAV) == 1, "nav ბლოკი ვერ მოიძებნა"
assert s.count(OLD_RET) == 1, "render_shell-ის return ვერ მოიძებნა"
s = s.replace(OLD_RET, '''    # QC_LINK — გადასვლა pacs-qc-ზე (admin / quality_control)
    qc_link = (
        '<div style="margin:14px 10px 0;padding-top:12px;border-top:1px solid var(--border);">'
        '<a class="nav-item" href="/pacs-qc/" style="display:flex;align-items:center;gap:8px;">'
        '<span style="width:20px;height:20px;border-radius:5px;background:linear-gradient(155deg,var(--danger),#8f1f24);'
        'color:#fff;font-size:9.5px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;">QC</span>'
        'Quality Control ↗</a></div>'
    ) if {"admin", "quality_control"}.intersection(user_roles) else ""
''' + OLD_RET, 1)
s = s.replace(OLD_NAV, '<nav class="nav">{nav_html}</nav>{qc_link}', 1)
open(p, "w", encoding="utf-8").write(s); print("OK")
__FIX__
cp pacs-admin/main.py pacs-admin/main.py.bak-$(date +%F-%H%M)
python3 /tmp/admin_qc_link.py pacs-admin/main.py
python3 -m py_compile pacs-admin/main.py && echo "COMPILE OK"
docker compose build pacs-admin && docker compose up -d --no-deps --force-recreate pacs-admin
sleep 4; docker compose exec nginx nginx -s reload 2>/dev/null || true
echo "✓ მზადაა → https://ris.innovamedical.ge/pacs-admin/"
