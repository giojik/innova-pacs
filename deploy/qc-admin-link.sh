#!/bin/bash
# pacs-qc: მენიუში „← PACS Admin“ ღილაკი (მხოლოდ admin როლზე)
# ბექაპი: pacs-qc/main.py.bak-<დრო>
set -e
cd /opt/pacs/25022026
cat > /tmp/qc_admin_link.py <<'__FIX__'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
if "ADMIN_LINK" in s:
    print("უკვე დამატებულია — გამოტოვება"); sys.exit(0)
OLD = '<div class="sidebar__title">PACS Quality Control</div>\n    </div>\n    <nav class="nav">{nav_html}</nav>'
assert s.count(OLD) == 1, "pacs-qc nav ბლოკი ვერ მოიძებნა"
s = s.replace(OLD, OLD + '{_admin_link()}', 1)
s += '''

# ==========================================================
# ADMIN_LINK — „← PACS Admin“ ღილაკი მენიუში (მხოლოდ admin როლზე)
# ==========================================================
import contextvars as _cv
_req_roles = _cv.ContextVar("_req_roles", default=())


@app.middleware("http")
async def _roles_ctx(request, call_next):
    claims = None
    try:
        claims = verify_doctor_token(request.cookies.get("doctor_token"))
    except Exception:
        pass
    tok = _req_roles.set(tuple(get_roles(claims)) if claims else ())
    try:
        return await call_next(request)
    finally:
        _req_roles.reset(tok)


def _admin_link():
    if "admin" not in _req_roles.get():
        return ""
    return ('<div style="margin:14px 10px 0;padding-top:12px;border-top:1px solid var(--border);">'
            '<a class="nav-item" href="/pacs-admin/" style="display:flex;align-items:center;gap:8px;">'
            '<span style="width:20px;height:20px;border-radius:5px;background:linear-gradient(155deg,#0E8F82,#0B7A6F);'
            'color:#fff;font-size:9.5px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;">PX</span>'
            '← PACS Admin</a></div>')
'''
open(p, "w", encoding="utf-8").write(s); print("OK")
__FIX__
cp pacs-qc/main.py pacs-qc/main.py.bak-$(date +%F-%H%M)
python3 /tmp/qc_admin_link.py pacs-qc/main.py
python3 -m py_compile pacs-qc/main.py && echo "COMPILE OK"
docker compose build pacs-qc && docker compose up -d --no-deps --force-recreate pacs-qc
sleep 4; docker compose exec nginx nginx -s reload 2>/dev/null || true
echo "✓ მზადაა → https://ris.innovamedical.ge/pacs-qc/"
