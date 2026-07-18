import os, json, csv, datetime, socket, html
from typing import List
import psycopg2
import requests
from fastapi import FastAPI, Cookie, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import jwt as _jwt
from jwt import PyJWKClient as _PyJWKClient

try:
    from ldap3 import Server, Connection, SUBTREE, ALL, MODIFY_REPLACE
    LDAP_AVAILABLE = True
except ImportError:
    LDAP_AVAILABLE = False

try:
    from pynetdicom import AE as _DicomAE
    from pynetdicom.sop_class import Verification as _DicomVerification
    DICOM_ECHO_AVAILABLE = True
except ImportError:
    DICOM_ECHO_AVAILABLE = False

app = FastAPI()

# ==========================================================
# კონფიგურაცია
# ==========================================================
KEYCLOAK_INTERNAL_URL = "http://keycloak:8080"          # შიდა docker ქსელის მისამართი — CORS არ გვჭირდება
KEYCLOAK_REALM = "dcm4che"
KEYCLOAK_JWKS_URL = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"

KC_SERVICE_CLIENT_ID = os.getenv("KEYCLOAK_ADMIN_CLIENT_ID", "")       # ცალკე, dcm4che realm-ში დარეგისტრირებული service account client
KC_SERVICE_CLIENT_SECRET = os.getenv("KEYCLOAK_ADMIN_CLIENT_SECRET", "")

DB_PARAMS = {
    "host": "db",
    "database": os.getenv("DB_NAME", "pacsdb"),
    "user": os.getenv("DB_USER", "pacs"),
    "password": os.getenv("DB_PASS", "pacs"),
}

LDAP_HOST = "ldap"
LDAP_PORT = 389
LDAP_BASE_DN = "dc=dcm4che,dc=org"
LDAP_BIND_DN = f"cn=admin,{LDAP_BASE_DN}"
LDAP_BIND_PASS = os.getenv("LDAP_ROOTPASS", "secret")
DEVICES_BASE_DN = f"cn=Devices,cn=DICOM Configuration,{LDAP_BASE_DN}"

DATA_PATH = "/app/data"                                   # volume-ით გაზიარებული pacs-portal მონაცემები
DOMAIN_NAME = os.getenv("DOMAIN_NAME", "")
CALLING_AE_TITLE = os.getenv("PING_CALLING_AE_TITLE", "PACSADMIN")   # C-ECHO ტესტისას "ვინ ურეკავს"

_jwks_client = None


def verify_doctor_token(token: str):
    """იგივე ვერიფიკაცია, რაც pacs-portal-ში — Keycloak-ის JWKS public key-ით
    ვამოწმებთ ხელმოწერასა და ვადას, უბრალო cookie-ს არსებობის ნაცვლად."""
    global _jwks_client
    if not token:
        return None
    try:
        if _jwks_client is None:
            _jwks_client = _PyJWKClient(KEYCLOAK_JWKS_URL)
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return _jwt.decode(token, signing_key.key, algorithms=["RS256"], options={"verify_aud": False})
    except Exception as e:
        print(f"doctor_token verification failed: {e}")
        return None


# ==========================================================
# RBAC — pacs-admin-ზე წვდომა როლის მიხედვით
# ==========================================================
# ექიმი (doctor) და დეპარტამენტის ხელმძღვანელი (department_head) საერთოდ ვერ ხედავენ
# pacs-admin-ს; ტექნიკოსი (technician) მხოლოდ Devices-ს; სრული წვდომა მხოლოდ admin-ს აქვს.
ROUTE_ROLES = {
    "/": {"admin"},
    "/users": {"admin"},
    "/sharing": {"admin"},
    "/devices": {"admin", "technician"},
    "/audit": {"admin"},
    "/settings": {"admin"},
}


def get_roles(claims):
    if not claims:
        return []
    return claims.get("realm_access", {}).get("roles", [])


def has_access(claims, path):
    allowed = ROUTE_ROLES.get(path, {"admin"})
    return bool(allowed.intersection(get_roles(claims)))


def render_403(user_roles=None):
    content = """
    <div class="notice" style="background:var(--danger-bg); color:var(--danger); border-color:var(--danger);">
      წვდომა აკრძალულია — ამ გვერდისთვის საჭირო როლი არ გაქვს მინიჭებული.
    </div>
    """
    return HTMLResponse(render_shell("", "წვდომა აკრძალულია", content, user_roles=user_roles), status_code=403)


# ==========================================================
# KEYCLOAK ADMIN (server-side, dedicated service account client — dcm4che realm only)
# ==========================================================
_kc_token_cache = {"token": None, "expires": 0}
DEFAULT_ROLE_NAMES = {f"default-roles-{KEYCLOAK_REALM}", "offline_access", "uma_authorization"}


def get_keycloak_admin_token():
    now = datetime.datetime.now().timestamp()
    if _kc_token_cache["token"] and _kc_token_cache["expires"] > now + 5:
        return _kc_token_cache["token"]
    if not KC_SERVICE_CLIENT_ID or not KC_SERVICE_CLIENT_SECRET:
        print("KEYCLOAK_ADMIN_CLIENT_ID/KEYCLOAK_ADMIN_CLIENT_SECRET არ არის მითითებული — Keycloak ინტეგრაცია გამორთულია")
        return None
    try:
        resp = requests.post(
            f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
            data={
                "client_id": KC_SERVICE_CLIENT_ID,
                "client_secret": KC_SERVICE_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        _kc_token_cache["token"] = data["access_token"]
        _kc_token_cache["expires"] = now + data.get("expires_in", 60)
        return _kc_token_cache["token"]
    except Exception as e:
        print(f"Keycloak admin token error: {e}")
        return None


def get_keycloak_roles():
    """realm-ის ყველა როლი (ინსტალაციის default roles-ის გამოკლებით) — role select-ის ჩამოსაშლელი სიისთვის."""
    token = get_keycloak_admin_token()
    if not token:
        return []
    try:
        resp = requests.get(
            f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{KEYCLOAK_REALM}/roles",
            headers={"Authorization": f"Bearer {token}"}, timeout=8,
        )
        resp.raise_for_status()
        return [r for r in resp.json() if r["name"] not in DEFAULT_ROLE_NAMES]
    except Exception as e:
        print(f"Keycloak roles fetch error: {e}")
        return []


def set_user_role(user_id: str, new_role_name: str):
    """შლის მომხმარებლის ამჟამინდელ (non-default) roles-ს და ანიჭებს ახალს."""
    token = get_keycloak_admin_token()
    if not token:
        return False, "Keycloak admin token ვერ მოხერხდა"
    headers = {"Authorization": f"Bearer {token}"}

    roles = get_keycloak_roles()
    new_role_obj = next((r for r in roles if r["name"] == new_role_name), None)
    if not new_role_obj:
        return False, f"როლი '{new_role_name}' ვერ მოიძებნა realm-ში"

    mapping_url = f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm"
    try:
        current = requests.get(mapping_url, headers=headers, timeout=5)
        current_roles = [r for r in current.json() if r["name"] not in DEFAULT_ROLE_NAMES] if current.ok else []
        if current_roles:
            requests.delete(mapping_url, headers=headers, json=current_roles, timeout=8)
        resp = requests.post(mapping_url, headers=headers, json=[new_role_obj], timeout=8)
        resp.raise_for_status()
        return True, None
    except Exception as e:
        print(f"Keycloak role assignment error: {e}")
        return False, str(e)


def get_keycloak_users():
    token = get_keycloak_admin_token()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(
            f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{KEYCLOAK_REALM}/users",
            headers=headers, params={"max": 200}, timeout=8,
        )
        resp.raise_for_status()
        kc_users = resp.json()
    except Exception as e:
        print(f"Keycloak users fetch error: {e}")
        return []

    result = []
    for u in kc_users:
        role_names = []
        try:
            rm = requests.get(
                f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{KEYCLOAK_REALM}/users/{u['id']}/role-mappings/realm",
                headers=headers, timeout=5,
            )
            if rm.ok:
                role_names = [r["name"] for r in rm.json() if r["name"] not in DEFAULT_ROLE_NAMES]
        except Exception:
            pass
        name = " ".join(filter(None, [u.get("firstName"), u.get("lastName")])) or u.get("username", "—")
        result.append({
            "id": u["id"],
            "name": name,
            "email": u.get("email") or "—",
            "role": role_names[0] if role_names else "—",
            "status": "აქტიური" if u.get("enabled") else "დაბლოკილი",
        })
    return result


# ==========================================================
# გაზიარების ლოგები (share_logs.csv)
# ==========================================================
def get_sharing_logs(limit=200):
    """კითხულობს pacs-portal-ის ნამდვილ share_logs.csv-ს — ყველა სვეტით,
    მათ შორის ადრესატის ემაილით (index 2), რომელსაც ძველი კოდი არ კითხულობდა."""
    logs = []
    file_path = f"{DATA_PATH}/share_logs.csv"
    if not os.path.exists(file_path):
        return logs
    try:
        with open(file_path, mode="r", encoding="utf-8-sig") as f:
            reader = list(csv.reader(f))
        rows = reader[1:][-limit:]
        rows.reverse()
        for r in rows:
            if len(r) < 7:
                continue
            time_, sender, recipient, patient, national_id, study_date, modality = r[:7]
            recipient_type = "თანამშრომელი" if (DOMAIN_NAME and DOMAIN_NAME in recipient) else "პაციენტი / გარე"
            logs.append({
                "time": time_, "sender": sender, "recipient": recipient,
                "patient": patient, "national_id": national_id,
                "study_date": study_date, "modality": modality,
                "recipient_type": recipient_type,
            })
    except Exception as e:
        print(f"Sharing logs read error: {e}")
    return logs


# ==========================================================
# მოწყობილობები (LDAP — dcm4chee DICOM Configuration schema)
# ==========================================================
LOCAL_HOST_ALIASES = {"localhost", "127.0.0.1", "::1"}


def ldap_val(entry, attr_name, default="—"):
    """ldap3-ის Attribute ობიექტიდან პირველ მნიშვნელობას იღებს, ცარიელი მასივის
    შემთხვევაში default-ს აბრუნებს (თორემ str() ცარიელ ატრიბუტს '[]'-ად აჩვენებდა)."""
    if attr_name in entry:
        values = entry[attr_name].values
        if values:
            return str(values[0])
    return default


def tcp_ping(host, port, timeout=1.5):
    """მარტივი TCP socket შემოწმება — მხოლოდ ამოწმებს, პორტი ღიაა თუ არა."""
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.close()
        return True, None
    except Exception as e:
        return False, str(e)


def get_devices():
    if not LDAP_AVAILABLE:
        print("ldap3 ბიბლიოთეკა არ არის დაინსტალირებული")
        return []
    devices = []
    try:
        server = Server(LDAP_HOST, port=LDAP_PORT, get_info=ALL)
        conn = Connection(server, LDAP_BIND_DN, LDAP_BIND_PASS, auto_bind=True)
        conn.search(
            DEVICES_BASE_DN, "(objectClass=dicomDevice)", SUBTREE,
            attributes=["dicomDeviceName", "dicomManufacturer", "dicomManufacturerModelName", "dicomDescription"],
        )
        for entry in conn.entries:
            device_dn = entry.entry_dn
            name = ldap_val(entry, "dicomDeviceName")

            conn.search(device_dn, "(objectClass=dicomNetworkAE)", SUBTREE, attributes=["dicomAETitle"])
            ae_titles = [ldap_val(e, "dicomAETitle") for e in conn.entries if "dicomAETitle" in e and e["dicomAETitle"].values]

            conn.search(device_dn, "(objectClass=dicomNetworkConnection)", SUBTREE, attributes=["dicomHostname", "dicomPort"])
            host, port = "—", None
            if conn.entries:
                first = conn.entries[0]
                host = ldap_val(first, "dicomHostname")
                port_str = ldap_val(first, "dicomPort", default="")
                try:
                    port = int(port_str) if port_str else None
                except ValueError:
                    port = None

            if host in LOCAL_HOST_ALIASES:
                # localhost/127.0.0.1 ეხება იმ სერვისის საკუთარ კონტეინერს, არა pacs-admin-ს —
                # pacs-admin-იდან ამ მისამართზე connectivity ტესტი ყოველთვის მცდარი იქნება.
                status = "უცნობი"
            elif host != "—" and port:
                ok, _ = tcp_ping(host, port)
                status = "ონლაინ" if ok else "შეცდომა"
            else:
                status = "ოფლაინ"

            devices.append({
                "name": name,
                "device_dn": device_dn,
                "manufacturer": ldap_val(entry, "dicomManufacturer"),
                "model": ldap_val(entry, "dicomManufacturerModelName"),
                "ae_titles": ", ".join(ae_titles) if ae_titles else "—",
                "ae_title_first": ae_titles[0] if ae_titles else "",
                "host": host,
                "port": port if port else "—",
                "status": status,
            })
        conn.unbind()
    except Exception as e:
        print(f"LDAP devices fetch error: {e}")
    return devices


def get_device_by_name(name):
    """ერთი მოწყობილობის სრული დეტალები (DN-ებით), edit ფორმის შესავსებად."""
    if not LDAP_AVAILABLE:
        return None
    try:
        server = Server(LDAP_HOST, port=LDAP_PORT, get_info=ALL)
        conn = Connection(server, LDAP_BIND_DN, LDAP_BIND_PASS, auto_bind=True)
        safe_name = name.replace("\\", "\\5c").replace("(", "\\28").replace(")", "\\29").replace("*", "\\2a")
        conn.search(
            DEVICES_BASE_DN, f"(&(objectClass=dicomDevice)(dicomDeviceName={safe_name}))", SUBTREE,
            attributes=["dicomDeviceName", "dicomManufacturer", "dicomManufacturerModelName"],
        )
        if not conn.entries:
            conn.unbind()
            return None
        entry = conn.entries[0]
        device_dn = entry.entry_dn

        conn.search(device_dn, "(objectClass=dicomNetworkAE)", SUBTREE, attributes=["dicomAETitle"])
        ae_dn, ae_title = None, ""
        if conn.entries:
            ae_dn = conn.entries[0].entry_dn
            ae_title = ldap_val(conn.entries[0], "dicomAETitle", default="")

        conn.search(device_dn, "(objectClass=dicomNetworkConnection)", SUBTREE, attributes=["dicomHostname", "dicomPort"])
        conn_dn, conn_host, conn_port = None, "", ""
        if conn.entries:
            conn_dn = conn.entries[0].entry_dn
            conn_host = ldap_val(conn.entries[0], "dicomHostname", default="")
            conn_port = ldap_val(conn.entries[0], "dicomPort", default="")

        result = {
            "name": name,
            "device_dn": device_dn,
            "manufacturer": ldap_val(entry, "dicomManufacturer", default=""),
            "model": ldap_val(entry, "dicomManufacturerModelName", default=""),
            "ae_dn": ae_dn, "ae_title": ae_title,
            "conn_dn": conn_dn, "host": conn_host, "port": conn_port,
        }
        conn.unbind()
        return result
    except Exception as e:
        print(f"LDAP get_device_by_name error: {e}")
        return None


LDAP_UNSAFE_CHARS = set(',+"\\<>;=\n\r')


def is_safe_ldap_name(value: str) -> bool:
    """თავს ვარიდებთ DN-ში სპეციალურ სიმბოლოებს — თუ სახელი/AE Title მათ შეიცავს,
    სჯობს ავიცილოთ დამახინჯებული DN-ის შექმნა, ვიდრე ვცადოთ სრული escaping."""
    return bool(value) and not any(ch in LDAP_UNSAFE_CHARS for ch in value)


def add_device(name, manufacturer, model, ae_title, host, port):
    if not LDAP_AVAILABLE:
        return False, "ldap3 ბიბლიოთეკა არ არის დაინსტალირებული"
    if not is_safe_ldap_name(name) or not is_safe_ldap_name(ae_title):
        return False, "სახელი/AE Title შეიცავს დაუშვებელ სიმბოლოს (, + \" \\ < > ; =)"
    try:
        server = Server(LDAP_HOST, port=LDAP_PORT, get_info=ALL)
        conn = Connection(server, LDAP_BIND_DN, LDAP_BIND_PASS, auto_bind=True)

        device_dn = f"dicomDeviceName={name},{DEVICES_BASE_DN}"
        device_attrs = {"dicomDeviceName": name, "dicomInstalled": True}
        if manufacturer:
            device_attrs["dicomManufacturer"] = manufacturer
        if model:
            device_attrs["dicomManufacturerModelName"] = model
        if not conn.add(device_dn, ["dicomDevice"], device_attrs):
            err = str(conn.result)
            conn.unbind()
            return False, f"device entry-ის შექმნა ვერ მოხერხდა: {err}"

        conn_dn = f"cn=conn1,{device_dn}"
        if not conn.add(conn_dn, ["dicomNetworkConnection"], {"cn": "conn1", "dicomHostname": host, "dicomPort": int(port)}):
            err = str(conn.result)
            conn.unbind()
            return False, f"connection entry-ის შექმნა ვერ მოხერხდა: {err}"

        ae_dn = f"dicomAETitle={ae_title},{device_dn}"
        ae_attrs = {
            "dicomAETitle": ae_title,
            "dicomAssociationInitiator": True,
            "dicomAssociationAcceptor": True,
            "dicomNetworkConnectionReference": [conn_dn],
        }
        if not conn.add(ae_dn, ["dicomNetworkAE"], ae_attrs):
            err = str(conn.result)
            conn.unbind()
            return False, f"AE Title entry-ის შექმნა ვერ მოხერხდა: {err}"

        conn.unbind()
        return True, None
    except Exception as e:
        return False, str(e)


def edit_device(device_dn, manufacturer, model, ae_dn, new_ae_title, conn_dn, host, port):
    if not LDAP_AVAILABLE:
        return False, "ldap3 ბიბლიოთეკა არ არის დაინსტალირებული"
    if new_ae_title and not is_safe_ldap_name(new_ae_title):
        return False, "AE Title შეიცავს დაუშვებელ სიმბოლოს"
    try:
        server = Server(LDAP_HOST, port=LDAP_PORT, get_info=ALL)
        conn = Connection(server, LDAP_BIND_DN, LDAP_BIND_PASS, auto_bind=True)

        changes = {}
        changes["dicomManufacturer"] = [(MODIFY_REPLACE, [manufacturer])] if manufacturer else [(MODIFY_REPLACE, [])]
        changes["dicomManufacturerModelName"] = [(MODIFY_REPLACE, [model])] if model else [(MODIFY_REPLACE, [])]
        conn.modify(device_dn, changes)

        if conn_dn and host:
            conn.modify(conn_dn, {
                "dicomHostname": [(MODIFY_REPLACE, [host])],
                "dicomPort": [(MODIFY_REPLACE, [int(port)])],
            })

        if ae_dn and new_ae_title:
            current_ae_title = ae_dn.split(",")[0].split("=", 1)[1]
            if current_ae_title != new_ae_title:
                conn.modify_dn(ae_dn, f"dicomAETitle={new_ae_title}")

        conn.unbind()
        return True, None
    except Exception as e:
        return False, str(e)


def dicom_echo(host, port, called_ae, timeout=4):
    """DICOM C-ECHO (Verification SOP Class) — რეალურად ამოწმებს, პასუხობს თუ არა
    მოწყობილობა DICOM ასოციაციის დონეზე, არა მხოლოდ TCP პორტის ღიაობას."""
    if not DICOM_ECHO_AVAILABLE:
        return False, "pynetdicom ბიბლიოთეკა არ არის დაინსტალირებული"
    try:
        ae = _DicomAE(ae_title=CALLING_AE_TITLE)
        ae.add_requested_context(_DicomVerification)
        ae.acse_timeout = timeout
        ae.dimse_timeout = timeout
        ae.network_timeout = timeout
        assoc = ae.associate(host, int(port), ae_title=called_ae)
        if assoc.is_established:
            status = assoc.send_c_echo()
            assoc.release()
            if status and status.Status == 0x0000:
                return True, None
            return False, f"C-ECHO სტატუსი: 0x{status.Status:04X}" if status else "პასუხი არ მოვიდა"
        return False, "DICOM ასოციაცია ვერ დამყარდა"
    except Exception as e:
        return False, str(e)


# ==========================================================
# აუდიტ ლოგი (Keycloak events + გაზიარების ლოგები, ქრონოლოგიურად შერწყმული)
# ==========================================================
def get_audit_entries(limit=80):
    entries = []
    token = get_keycloak_admin_token()
    if token:
        try:
            resp = requests.get(
                f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{KEYCLOAK_REALM}/events",
                headers={"Authorization": f"Bearer {token}"},
                params={"max": limit},
                timeout=8,
            )
            if resp.ok:
                for ev in resp.json():
                    ev_type = ev.get("type", "—")
                    entries.append({
                        "time": datetime.datetime.fromtimestamp(ev.get("time", 0) / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                        "user": ev.get("userId", "—"),
                        "action": ev_type,
                        "target": ev.get("clientId", "—"),
                        "ip": ev.get("ipAddress", "—"),
                        "result": "fail" if "ERROR" in ev_type else "success",
                    })
            else:
                print(f"Keycloak events endpoint returned {resp.status_code} — შესაძლოა event logging გამორთულია realm-ში")
        except Exception as e:
            print(f"Keycloak events fetch error: {e}")

    for log in get_sharing_logs(limit):
        entries.append({
            "time": log["time"], "user": log["sender"], "action": "გაზიარება",
            "target": f'{log["patient"]} ({log["modality"]}) → {log["recipient"]}',
            "ip": "—", "result": "success",
        })

    entries.sort(key=lambda e: e["time"], reverse=True)
    return entries[:limit]


# ==========================================================
# dcm4chee-arc სტატისტიკა (Postgres)
# ==========================================================
def get_stats(period="month"):
    intervals = {"day": "1 day", "week": "7 days", "month": "30 days", "year": "1 year"}
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cur = conn.cursor()
        query = f"""
            SELECT series.modality, COUNT(DISTINCT study.pk)
            FROM study
            JOIN series ON series.study_fk = study.pk
            WHERE study.created_time > CURRENT_TIMESTAMP - INTERVAL '{intervals.get(period, "30 days")}'
            GROUP BY series.modality;
        """
        cur.execute(query)
        mod_stats = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM study")
        total_all = cur.fetchone()[0]
        cur.close()
        conn.close()
        return mod_stats, total_all
    except Exception as e:
        print(f"DB stats error: {e}")
        return [], 0


def read_json(filename):
    try:
        with open(f"{DATA_PATH}/{filename}", "r") as f:
            return json.load(f)
    except Exception:
        return {}


# ==========================================================
# UI SHELL — sidebar + light theme (იზიარებს ერთ CSS-ს ყველა გვერდზე)
# ==========================================================
URL_PREFIX = os.getenv("ADMIN_URL_PREFIX", "/pacs-admin").rstrip("/")  # nginx-ის location პრეფიქსი

NAV_ITEMS = [
    ("/", "დაშბორდი"),
    ("/users", "მომხმარებლები"),
    ("/sharing", "გაზიარების ლოგები"),
    ("/devices", "სამედიცინო აპარატურა"),
    ("/audit", "აუდიტ ლოგი"),
    ("/settings", "სეთინგები"),
]

BASE_CSS = """
:root{
  --bg:#EEF2F5; --sidebar:#FFFFFF; --surface:#FFFFFF; --surface-2:#F3F6F8;
  --surface-hover:#E9EEF2; --border:#DCE3E9; --text:#1E2933; --text-muted:#66768A;
  --text-faint:#93A2AD; --accent:#0E8F82; --accent-text:#FFFFFF;
  --success:#1E8E4F; --success-bg:#E6F6EC; --warning:#B7791F; --warning-bg:#FCF1D8;
  --danger:#D0393E; --danger-bg:#FBE9E9; --info:#2F6FE4; --info-bg:#E8EFFD; --radius:8px;
}
*{box-sizing:border-box; margin:0; padding:0;}
body{background:var(--bg); color:var(--text); font-family:'Inter',sans-serif; font-size:14px;}
.app{display:flex; min-height:100vh;}
.sidebar{width:240px; flex-shrink:0; background:var(--sidebar); border-right:1px solid var(--border); padding:20px 0;}
.sidebar__brand{display:flex; align-items:center; gap:10px; padding:0 20px 18px 20px; border-bottom:1px solid var(--border); margin-bottom:10px;}
.sidebar__mark{width:32px; height:32px; border-radius:7px; background:linear-gradient(155deg, var(--accent), #0B7A6F); display:flex; align-items:center; justify-content:center; font-weight:700; font-size:14px; color:#fff;}
.sidebar__title{font-weight:600; font-size:15px;}
.nav{display:flex; flex-direction:column; gap:2px; padding:0 10px;}
.nav-item{display:block; padding:9px 12px; border-radius:7px; color:var(--text-muted); text-decoration:none; font-size:13.5px; font-weight:500;}
.nav-item:hover{background:var(--surface-hover); color:var(--text);}
.nav-item.active{background:var(--surface-2); color:var(--text); border:1px solid var(--border);}
.main{flex:1; min-width:0;}
.topbar{height:56px; border-bottom:1px solid var(--border); display:flex; align-items:center; padding:0 26px; font-weight:600; font-size:17px;}
.content{padding:24px 26px 50px 26px;}
.panel{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:18px 20px; margin-bottom:16px;}
.panel__title{font-weight:600; font-size:14.5px; margin-bottom:4px;}
.panel__sub{font-size:11.5px; color:var(--text-faint); margin-bottom:12px;}
.stat-grid{display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:20px;}
.stat-card{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); padding:16px 18px;}
.stat-card__label{font-size:12px; color:var(--text-muted); font-weight:500; margin-bottom:8px;}
.stat-card__value{font-size:24px; font-weight:600;}
table{width:100%; border-collapse:collapse;}
thead th{text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--text-faint); font-weight:600; padding:11px 14px; border-bottom:1px solid var(--border); background:var(--surface-2);}
tbody td{padding:11px 14px; border-bottom:1px solid var(--border); font-size:13px;}
tbody tr:last-child td{border-bottom:none;}
tbody tr:hover{background:var(--surface-2);}
.table-wrap{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); overflow:hidden;}
.cell-muted{color:var(--text-muted);}
.cell-mono{font-family:monospace; font-size:12px; color:var(--text-muted);}
.badge{display:inline-flex; align-items:center; gap:5px; padding:3px 9px; border-radius:20px; font-size:11.5px; font-weight:500; white-space:nowrap;}
.badge-success{background:var(--success-bg); color:var(--success);}
.badge-warning{background:var(--warning-bg); color:var(--warning);}
.badge-danger{background:var(--danger-bg); color:var(--danger);}
.badge-info{background:var(--info-bg); color:var(--info);}
.badge-neutral{background:var(--surface-2); color:var(--text-muted); border:1px solid var(--border);}
.role-select{background:var(--surface-2); border:1px solid var(--border); color:var(--text); font-size:12.5px; padding:5px 9px; border-radius:6px; font-family:inherit;}
.select-input, .text-input{background:var(--surface); border:1px solid var(--border); color:var(--text); font-size:13px; padding:7px 10px; border-radius:7px; font-family:inherit;}
.empty-state{text-align:center; padding:40px 20px; color:var(--text-faint); font-size:13px;}
.notice{font-size:12px; color:var(--text-faint); background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius); padding:10px 14px; margin-bottom:14px;}
.toolbar{display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; gap:12px; flex-wrap:wrap;}
.btn{display:inline-flex; align-items:center; gap:6px; padding:8px 14px; border-radius:7px; font-size:13px; font-weight:500; cursor:pointer; border:1px solid var(--border); background:var(--surface); color:var(--text); text-decoration:none;}
.btn:hover{background:var(--surface-hover);}
"""


def status_badge(status):
    if status == "აქტიური":
        return '<span class="badge badge-success">აქტიური</span>'
    if status == "დაბლოკილი":
        return '<span class="badge badge-danger">დაბლოკილი</span>'
    return f'<span class="badge badge-neutral">{html.escape(str(status))}</span>'


def device_status_badge(status):
    if status == "ონლაინ":
        return '<span class="badge badge-success">ონლაინ</span>'
    if status == "ოფლაინ":
        return '<span class="badge badge-neutral">ოფლაინ</span>'
    if status == "უცნობი":
        return '<span class="badge badge-info" title="localhost/127.0.0.1 — ვერ მოწმდება pacs-admin-იდან">უცნობი</span>'
    return '<span class="badge badge-danger">შეცდომა</span>'


def result_badge(result):
    return ('<span class="badge badge-success">წარმატებული</span>' if result == "success"
            else '<span class="badge badge-danger">ჩავარდნილი</span>')


def render_shell(active_path: str, page_title: str, content: str, user_roles=None) -> str:
    user_roles = user_roles or []
    visible_items = [
        (path, label) for path, label in NAV_ITEMS
        if set(ROUTE_ROLES.get(path, {"admin"})).intersection(user_roles)
    ]
    nav_html = "".join(
        f'<a class="nav-item {"active" if path == active_path else ""}" href="{URL_PREFIX}{path}">{label}</a>'
        for path, label in visible_items
    )
    return f"""<!DOCTYPE html>
<html lang="ka"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PACS Admin — {html.escape(page_title)}</title>
<style>{BASE_CSS}</style>
</head><body>
<div class="app">
  <aside class="sidebar">
    <div class="sidebar__brand">
      <div class="sidebar__mark">PX</div>
      <div class="sidebar__title">PACS Admin</div>
    </div>
    <nav class="nav">{nav_html}</nav>
  </aside>
  <main class="main">
    <div class="topbar">{html.escape(page_title)}</div>
    <div class="content">{content}</div>
  </main>
</div>
</body></html>"""


# ==========================================================
# ROUTES
# ==========================================================
@app.get("/", response_class=HTMLResponse)
async def dashboard(period: str = "month", doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/"):
        return render_403(get_roles(claims))

    mod_stats, total_all = get_stats(period)
    live_sync = read_json("live_sync_active.json")
    migration = read_json("active_transfers.json")
    recent_logs = get_sharing_logs(5)

    stats_html = "".join(
        f'<div class="stat-card"><div class="stat-card__label">{html.escape(str(m))}</div><div class="stat-card__value">{html.escape(str(c))}</div></div>'
        for m, c in mod_stats
    ) or '<div class="empty-state">ამ პერიოდზე მონაცემი არ არის</div>'

    log_rows = "".join(
        f'<tr><td class="cell-mono">{html.escape(l["time"])}</td><td>{html.escape(l["sender"])}</td>'
        f'<td class="cell-muted">{html.escape(l["patient"])}</td><td class="cell-mono">{html.escape(l["recipient"])}</td>'
        f'<td>{html.escape(l["modality"])}</td></tr>'
        for l in recent_logs
    ) or '<tr><td colspan="5"><div class="empty-state">ლოგები არ მოიძებნა</div></td></tr>'

    content = f"""
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-card__label">სულ კვლევები</div><div class="stat-card__value">{total_all}</div></div>
      <div class="stat-card"><div class="stat-card__label">Live Sync</div><div class="stat-card__value">{len(live_sync)}</div></div>
      <div class="stat-card"><div class="stat-card__label">ღამის მიგრაცია</div><div class="stat-card__value">{len(migration)}</div></div>
      <div class="stat-card"><div class="stat-card__label">გაზიარება (ბოლო)</div><div class="stat-card__value">{len(recent_logs)}</div></div>
    </div>
    <div class="panel">
      <div class="panel__title">მოდალობის სტატისტიკა — {period}</div>
      <div class="panel__sub">
        <a href="{URL_PREFIX}/?period=day">დღე</a> · <a href="{URL_PREFIX}/?period=week">კვირა</a> · <a href="{URL_PREFIX}/?period=month">თვე</a>
      </div>
      <div class="stat-grid" style="margin-bottom:0;">{stats_html}</div>
    </div>
    <div class="panel">
      <div class="panel__title">ბოლო გაზიარებები</div>
      <div class="table-wrap"><table>
        <thead><tr><th>დრო</th><th>გამზიარებელი</th><th>პაციენტი</th><th>ადრესატი</th><th>მოდალობა</th></tr></thead>
        <tbody>{log_rows}</tbody>
      </table></div>
    </div>
    """
    return render_shell("/", "დაშბორდი", content, user_roles=get_roles(claims))


@app.get("/users", response_class=HTMLResponse)
async def users_page(doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/users"):
        return render_403(get_roles(claims))

    users = get_keycloak_users()
    notice = ""
    if not KC_SERVICE_CLIENT_ID or not KC_SERVICE_CLIENT_SECRET:
        notice = '<div class="notice">Keycloak-თან დაკავშირება არ არის კონფიგურირებული — დაამატე KEYCLOAK_ADMIN_CLIENT_ID/KEYCLOAK_ADMIN_CLIENT_SECRET docker-compose-ის pacs-admin სერვისში.</div>'
    elif not users:
        notice = '<div class="notice">მომხმარებელი ვერ წამოვიღეთ Keycloak-იდან — შეამოწმე ლოგები (docker logs pacs-admin) კავშირის დეტალებისთვის.</div>'

    roles = get_keycloak_roles()

    def role_options(current_role, blank_first=False):
        opts = ['<option value="">— აირჩიე —</option>'] if blank_first else []
        if not roles:
            opts.append(f'<option>{html.escape(current_role)}</option>')
        else:
            opts += [
                f'<option value="{html.escape(r["name"])}" {"selected" if r["name"] == current_role else ""}>{html.escape(r["name"])}</option>'
                for r in roles
            ]
        return "".join(opts)

    def role_filter_options():
        opts = ['<option value="">ყველა როლი</option>']
        opts += [f'<option value="{html.escape(r["name"])}">{html.escape(r["name"])}</option>' for r in roles]
        return "".join(opts)

    rows = "".join(
        f'<tr data-role="{html.escape(u["role"])}" data-status="{html.escape(u["status"])}">'
        f'<td><input type="checkbox" name="user_ids" form="bulkRoleForm" value="{html.escape(u["id"])}"></td>'
        f'<td>{html.escape(u["name"])}</td><td class="cell-muted">{html.escape(u["email"])}</td>'
        f'<td><select class="role-select" onchange="submitRoleChange(this)" data-user-id="{html.escape(u["id"])}">{role_options(u["role"])}</select></td>'
        f'<td>{status_badge(u["status"])}</td></tr>'
        for u in users
    ) or '<tr><td colspan="5"><div class="empty-state">მომხმარებელი ვერ მოიძებნა</div></td></tr>'

    content = f"""
    {notice}
    <div style="display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap;">
      <input type="text" id="userSearchInput" class="text-input" placeholder="ძებნა სახელით ან ემაილით..." oninput="applyUserFilters()" style="width:260px;">
      <select id="userRoleFilter" class="select-input" onchange="applyUserFilters()">{role_filter_options()}</select>
      <select id="userStatusFilter" class="select-input" onchange="applyUserFilters()">
        <option value="">ყველა სტატუსი</option>
        <option value="აქტიური">აქტიური</option>
        <option value="დაბლოკილი">დაბლოკილი</option>
      </select>
      <span id="userSearchCount" style="font-size:12px; color:var(--text-faint);"></span>
    </div>
    <form method="post" action="{URL_PREFIX}/users/bulk-role" id="bulkRoleForm">
      <div class="panel">
        <div class="panel__title">Keycloak მომხმარებლები</div>
        <div class="panel__sub">realm: {KEYCLOAK_REALM} · სულ {len(users)} მომხმარებელი</div>
        <div style="display:flex; gap:8px; align-items:center; margin-bottom:14px;">
          <span style="font-size:12.5px; color:var(--text-muted);">მონიშნულებზე მინიჭება:</span>
          <select name="role_name" class="role-select" required>{role_options("", blank_first=True)}</select>
          <button type="submit" class="btn" style="background:var(--accent); color:var(--accent-text); border-color:var(--accent);"
            onclick="return confirm('დარწმუნებული ხარ, რომ ამ როლს მიანიჭებ ყველა მონიშნულ მომხმარებელს?')">
            მინიჭება მონიშნულებზე
          </button>
        </div>
        <div class="table-wrap"><table>
          <thead><tr><th style="width:32px;"><input type="checkbox" onclick="toggleAllUsers(this)"></th><th>მომხმარებელი</th><th>ემაილი</th><th>როლი</th><th>სტატუსი</th></tr></thead>
          <tbody id="usersTableBody">{rows}</tbody>
        </table></div>
      </div>
    </form>
    <script>
    function toggleAllUsers(master) {{
      document.querySelectorAll('#usersTableBody tr').forEach(function(tr) {{
        if (tr.style.display !== 'none') {{
          var cb = tr.querySelector('input[name="user_ids"]');
          if (cb) cb.checked = master.checked;
        }}
      }});
    }}
    function applyUserFilters() {{
      var query = document.getElementById('userSearchInput').value.toLowerCase();
      var roleFilter = document.getElementById('userRoleFilter').value;
      var statusFilter = document.getElementById('userStatusFilter').value;
      var visible = 0;
      document.querySelectorAll('#usersTableBody tr').forEach(function(tr) {{
        var text = tr.textContent.toLowerCase();
        var matchQuery = !query || text.indexOf(query) !== -1;
        var matchRole = !roleFilter || tr.getAttribute('data-role') === roleFilter;
        var matchStatus = !statusFilter || tr.getAttribute('data-status') === statusFilter;
        var show = matchQuery && matchRole && matchStatus;
        tr.style.display = show ? '' : 'none';
        if (show) visible++;
      }});
      document.getElementById('userSearchCount').textContent = 'ნაპოვნია: ' + visible;
    }}
    function submitRoleChange(sel) {{
      var uid = sel.getAttribute('data-user-id');
      var role = sel.value;
      fetch(window.location.pathname + '/' + uid + '/role', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: 'role_name=' + encodeURIComponent(role)
      }}).then(function() {{ location.reload(); }});
    }}
    </script>
    """
    return render_shell("/users", "მომხმარებლები", content, user_roles=get_roles(claims))


@app.post("/users/{user_id}/role")
async def update_user_role(user_id: str, role_name: str = Form(...), doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login", status_code=303)
    if not has_access(claims, "/users"):
        return render_403(get_roles(claims))
    ok, err = set_user_role(user_id, role_name)
    if not ok:
        print(f"როლის მინიჭება ვერ მოხერხდა user_id={user_id}: {err}")
    return RedirectResponse(url=f"{URL_PREFIX}/users", status_code=303)


@app.post("/users/bulk-role")
async def bulk_update_role(user_ids: List[str] = Form(...), role_name: str = Form(...), doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login", status_code=303)
    if not has_access(claims, "/users"):
        return render_403(get_roles(claims))
    if not role_name:
        return RedirectResponse(url=f"{URL_PREFIX}/users", status_code=303)
    for uid in user_ids:
        ok, err = set_user_role(uid, role_name)
        if not ok:
            print(f"ჯგუფური როლის მინიჭება ვერ მოხერხდა user_id={uid}: {err}")
    return RedirectResponse(url=f"{URL_PREFIX}/users", status_code=303)


@app.get("/sharing", response_class=HTMLResponse)
async def sharing_page(doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/sharing"):
        return render_403(get_roles(claims))

    logs = get_sharing_logs()
    rows = "".join(
        f'<tr><td class="cell-mono">{html.escape(l["time"])}</td><td>{html.escape(l["sender"])}</td>'
        f'<td class="cell-muted">{html.escape(l["patient"])}</td><td class="cell-mono">{html.escape(l["national_id"])}</td>'
        f'<td class="cell-mono">{html.escape(l["recipient"])}</td>'
        f'<td><span class="badge badge-neutral">{html.escape(l["recipient_type"])}</span></td>'
        f'<td>{html.escape(l["modality"])}</td></tr>'
        for l in logs
    ) or '<tr><td colspan="7"><div class="empty-state">ჩანაწერი ვერ მოიძებნა</div></td></tr>'

    content = f"""
    <div class="panel">
      <div class="panel__title">გაზიარების ლოგები</div>
      <div class="panel__sub">წყარო: share_logs.csv · სულ {len(logs)} ჩანაწერი</div>
      <div class="table-wrap"><table>
        <thead><tr><th>დრო</th><th>გამზიარებელი (ექიმი)</th><th>პაციენტი</th><th>პირადი №</th><th>ადრესატი (ემაილი)</th><th>ტიპი</th><th>მოდალობა</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>
    """
    return render_shell("/sharing", "გაზიარების ლოგები", content, user_roles=get_roles(claims))


@app.get("/devices", response_class=HTMLResponse)
async def devices_page(doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/devices"):
        return render_403(get_roles(claims))

    devices = get_devices()
    notice = ""
    if not LDAP_AVAILABLE:
        notice = '<div class="notice">ldap3 ბიბლიოთეკა არ არის დაინსტალირებული კონტეინერში — დაამატე Dockerfile-ში.</div>'
    elif not devices:
        notice = '<div class="notice">მოწყობილობა ვერ მოიძებნა LDAP-ში — თუ ეს პირველი გაშვებაა, შეამოწმე DEVICES_BASE_DN ბაზისეულ სქემასთან შესაბამისობა (docker logs pacs-admin).</div>'

    rows = "".join(
        f'<tr data-host="{html.escape(d["host"])}" data-port="{html.escape(str(d["port"]))}" data-ae="{html.escape(d["ae_title_first"])}">'
        f'<td>{html.escape(d["name"])}</td><td class="cell-muted">{html.escape(d["manufacturer"])}</td>'
        f'<td class="cell-muted">{html.escape(d["model"])}</td><td class="cell-mono">{html.escape(d["ae_titles"])}</td>'
        f'<td class="cell-mono">{html.escape(d["host"])}:{html.escape(str(d["port"]))}</td>'
        f'<td>{device_status_badge(d["status"])}</td>'
        f'<td>'
        f'<div style="display:flex; gap:5px; align-items:center; flex-wrap:wrap;">'
        f'<a class="btn" style="padding:5px 9px; font-size:12px;" href="{URL_PREFIX}/devices/{html.escape(d["name"])}/edit">რედაქტირება</a>'
        f'<button type="button" class="btn" style="padding:5px 9px; font-size:12px;" onclick="testDevice(this,\'ping\')">Ping</button>'
        f'<button type="button" class="btn" style="padding:5px 9px; font-size:12px;" onclick="testDevice(this,\'echo\')">Echo</button>'
        f'<span class="test-result" style="font-size:11px;"></span>'
        f'</div>'
        f'</td></tr>'
        for d in devices
    ) or '<tr><td colspan="7"><div class="empty-state">მოწყობილობა ვერ მოიძებნა</div></td></tr>'

    if not DICOM_ECHO_AVAILABLE:
        notice += '<div class="notice">pynetdicom ბიბლიოთეკა არ არის დაინსტალირებული — Echo (C-ECHO) ღილაკი არ იმუშავებს, სანამ Dockerfile-ს არ განაახლებ.</div>'

    content = f"""
    {notice}
    <div class="toolbar" style="margin-bottom:14px;">
      <div></div>
      <a class="btn" style="background:var(--accent); color:var(--accent-text); border-color:var(--accent);" href="{URL_PREFIX}/devices/new">
        + აპარატის დამატება
      </a>
    </div>
    <div class="panel">
      <div class="panel__title">სამედიცინო აპარატურა</div>
      <div class="panel__sub">წყარო: LDAP (dcm4chee DICOM Configuration) · სულ {len(devices)} მოწყობილობა</div>
      <div class="table-wrap"><table>
        <thead><tr><th>სახელი</th><th>მწარმოებელი</th><th>მოდელი</th><th>AE Title(s)</th><th>Host:Port</th><th>სტატუსი</th><th>მოქმედება</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>
    <script>
    function testDevice(btn, kind) {{
      var tr = btn.closest('tr');
      var host = tr.getAttribute('data-host');
      var port = tr.getAttribute('data-port');
      var ae = tr.getAttribute('data-ae');
      var resultEl = tr.querySelector('.test-result');
      resultEl.textContent = '...';
      resultEl.style.color = 'var(--text-faint)';
      var body = 'host=' + encodeURIComponent(host) + '&port=' + encodeURIComponent(port);
      if (kind === 'echo') {{ body += '&ae_title=' + encodeURIComponent(ae); }}
      fetch(window.location.pathname + '/' + kind, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: body
      }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
        resultEl.textContent = data.ok ? '✓ OK' : ('✗ ' + (data.error || 'შეცდომა'));
        resultEl.style.color = data.ok ? 'var(--success)' : 'var(--danger)';
      }}).catch(function() {{
        resultEl.textContent = '✗ ქსელის შეცდომა';
        resultEl.style.color = 'var(--danger)';
      }});
    }}
    </script>
    """
    return render_shell("/devices", "სამედიცინო აპარატურა", content, user_roles=get_roles(claims))


def device_form_html(action, title, device=None):
    device = device or {}
    name_readonly = "readonly" if device else ""
    return f"""
    <div class="panel" style="max-width:540px;">
      <div class="panel__title">{html.escape(title)}</div>
      <form method="post" action="{action}">
        <div style="margin-bottom:12px;">
          <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">აპარატის სახელი</label>
          <input type="text" name="name" class="text-input" style="width:100%;" value="{html.escape(device.get('name', ''))}" {name_readonly} required>
        </div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
          <div>
            <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">მწარმოებელი</label>
            <input type="text" name="manufacturer" class="text-input" style="width:100%;" value="{html.escape(device.get('manufacturer', ''))}">
          </div>
          <div>
            <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">მოდელი</label>
            <input type="text" name="model" class="text-input" style="width:100%;" value="{html.escape(device.get('model', ''))}">
          </div>
        </div>
        <div style="margin-bottom:12px;">
          <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">AE Title</label>
          <input type="text" name="ae_title" class="text-input" style="width:100%;" value="{html.escape(device.get('ae_title', ''))}" required>
        </div>
        <div style="display:grid; grid-template-columns:2fr 1fr; gap:12px; margin-bottom:18px;">
          <div>
            <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">Host</label>
            <input type="text" name="host" class="text-input" style="width:100%;" value="{html.escape(device.get('host', ''))}" required>
          </div>
          <div>
            <label style="display:block; font-size:12px; color:var(--text-muted); margin-bottom:5px;">Port</label>
            <input type="number" name="port" class="text-input" style="width:100%;" value="{html.escape(str(device.get('port', '')))}" required>
          </div>
        </div>
        <div style="display:flex; gap:8px; justify-content:flex-end;">
          <a href="{URL_PREFIX}/devices" class="btn">გაუქმება</a>
          <button type="submit" class="btn" style="background:var(--accent); color:var(--accent-text); border-color:var(--accent);">შენახვა</button>
        </div>
      </form>
    </div>
    """


@app.get("/devices/new", response_class=HTMLResponse)
async def new_device_form(doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/devices"):
        return render_403(get_roles(claims))
    content = device_form_html(f"{URL_PREFIX}/devices/new", "ახალი აპარატის დამატება")
    return render_shell("/devices", "აპარატის დამატება", content, user_roles=get_roles(claims))


@app.post("/devices/new")
async def create_device(
    name: str = Form(...), manufacturer: str = Form(""), model: str = Form(""),
    ae_title: str = Form(...), host: str = Form(...), port: int = Form(...),
    doctor_token: str = Cookie(None),
):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login", status_code=303)
    if not has_access(claims, "/devices"):
        return render_403(get_roles(claims))
    ok, err = add_device(name, manufacturer.strip(), model.strip(), ae_title, host, port)
    if not ok:
        print(f"აპარატის დამატება ვერ მოხერხდა: {err}")
    return RedirectResponse(url=f"{URL_PREFIX}/devices", status_code=303)


@app.get("/devices/{device_name}/edit", response_class=HTMLResponse)
async def edit_device_form(device_name: str, doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/devices"):
        return render_403(get_roles(claims))
    device = get_device_by_name(device_name)
    if not device:
        return RedirectResponse(url=f"{URL_PREFIX}/devices", status_code=303)
    content = device_form_html(f"{URL_PREFIX}/devices/{device_name}/edit", "აპარატის რედაქტირება", device=device)
    return render_shell("/devices", "აპარატის რედაქტირება", content, user_roles=get_roles(claims))


@app.post("/devices/{device_name}/edit")
async def update_device(
    device_name: str, manufacturer: str = Form(""), model: str = Form(""),
    ae_title: str = Form(...), host: str = Form(...), port: int = Form(...),
    doctor_token: str = Cookie(None),
):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login", status_code=303)
    if not has_access(claims, "/devices"):
        return render_403(get_roles(claims))
    device = get_device_by_name(device_name)
    if not device:
        return RedirectResponse(url=f"{URL_PREFIX}/devices", status_code=303)
    ok, err = edit_device(
        device["device_dn"], manufacturer.strip(), model.strip(),
        device.get("ae_dn"), ae_title, device.get("conn_dn"), host, port,
    )
    if not ok:
        print(f"აპარატის რედაქტირება ვერ მოხერხდა ({device_name}): {err}")
    return RedirectResponse(url=f"{URL_PREFIX}/devices", status_code=303)


@app.post("/devices/ping")
async def ping_device_endpoint(host: str = Form(...), port: int = Form(...), doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims or not has_access(claims, "/devices"):
        return JSONResponse({"ok": False, "error": "წვდომა აკრძალულია"}, status_code=403)
    ok, err = tcp_ping(host, port)
    return JSONResponse({"ok": ok, "error": err})


@app.post("/devices/echo")
async def echo_device_endpoint(host: str = Form(...), port: int = Form(...), ae_title: str = Form(""), doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims or not has_access(claims, "/devices"):
        return JSONResponse({"ok": False, "error": "წვდომა აკრძალულია"}, status_code=403)
    if not ae_title:
        return JSONResponse({"ok": False, "error": "AE Title არ არის ცნობილი ამ მოწყობილობისთვის"})
    ok, err = dicom_echo(host, port, ae_title)
    return JSONResponse({"ok": ok, "error": err})


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/audit"):
        return render_403(get_roles(claims))

    entries = get_audit_entries()
    rows = "".join(
        f'<tr><td class="cell-mono">{html.escape(e["time"])}</td><td>{html.escape(str(e["user"]))}</td>'
        f'<td>{html.escape(e["action"])}</td><td class="cell-muted">{html.escape(str(e["target"]))}</td>'
        f'<td class="cell-mono">{html.escape(str(e["ip"]))}</td><td>{result_badge(e["result"])}</td></tr>'
        for e in entries
    ) or '<tr><td colspan="6"><div class="empty-state">ჩანაწერი ვერ მოიძებნა</div></td></tr>'

    content = f"""
    <div class="notice">აუდიტ ლოგი აერთიანებს Keycloak-ის login/logout მოვლენებს (თუ realm-ში ჩართულია event logging) და გაზიარების ლოგებს ერთ ქრონოლოგიურ სიაში.</div>
    <div class="panel">
      <div class="panel__title">აუდიტ ლოგი</div>
      <div class="panel__sub">სულ {len(entries)} ჩანაწერი</div>
      <div class="table-wrap"><table>
        <thead><tr><th>დრო</th><th>მომხმარებელი</th><th>მოქმედება</th><th>სამიზნე</th><th>IP</th><th>შედეგი</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </div>
    """
    return render_shell("/audit", "აუდიტ ლოგი", content, user_roles=get_roles(claims))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(doctor_token: str = Cookie(None)):
    claims = verify_doctor_token(doctor_token)
    if not claims:
        return RedirectResponse(url="/login")
    if not has_access(claims, "/settings"):
        return render_403(get_roles(claims))

    content = f"""
    <div class="notice">სეთინგები ამჟამად მხოლოდ საკითხავია (docker-compose environment ცვლადებიდან). რედაქტირებადი პარამეტრებისთვის საჭირო იქნება ცალკე კონფიგურაციის საცავი.</div>
    <div class="panel">
      <div class="panel__title">სისტემის ინფორმაცია</div>
      <div class="table-wrap"><table>
        <tbody>
          <tr><td class="cell-muted">Domain</td><td class="cell-mono">{html.escape(DOMAIN_NAME or "—")}</td></tr>
          <tr><td class="cell-muted">Keycloak realm</td><td class="cell-mono">{html.escape(KEYCLOAK_REALM)}</td></tr>
          <tr><td class="cell-muted">Database</td><td class="cell-mono">{html.escape(DB_PARAMS["database"])}</td></tr>
          <tr><td class="cell-muted">LDAP base DN</td><td class="cell-mono">{html.escape(LDAP_BASE_DN)}</td></tr>
        </tbody>
      </table></div>
    </div>
    """
    return render_shell("/settings", "სეთინგები", content, user_roles=get_roles(claims))