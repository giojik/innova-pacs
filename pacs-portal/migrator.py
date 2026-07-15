import os, requests, asyncio, datetime, json, time, urllib3

# SSL გაფრთხილებების გათიშვა
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 1. კონფიგურაცია
# ==========================================
AE_TITLE = os.getenv("AE_TITLE", "RISINNOVA")
OLD_PACS_RS = os.getenv("OLD_PACS_URL")
# ახალი PACS-ის შიდა მისამართი
NEW_PACS_RS = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"

OLD_TOKEN_URL = os.getenv("OLD_KEYCLOAK_URL")
OLD_CLIENT_ID = os.getenv("OLD_CLIENT_ID")
OLD_CLIENT_SECRET = os.getenv("OLD_CLIENT_SECRET")

PROGRESS_FILE = "/app/migrated_uids.txt"
STATUS_FILE = "/app/migration_status.txt"
ACTIVE_JSON = "/app/active_transfers.json"

# დროის პარამეტრები
START_HOUR = 0  # 00:00
END_HOUR = 24    # 07:00

# ლიმიტი: 3 ერთდროული ტრანსფერი
semaphore = asyncio.Semaphore(3)

# ==========================================
# 2. დამხმარე ფუნქციები
# ==========================================

def get_token():
    try:
        data = {
            "client_id": OLD_CLIENT_ID, 
            "client_secret": OLD_CLIENT_SECRET, 
            "grant_type": "client_credentials"
        }
        r = requests.post(OLD_TOKEN_URL, data=data, timeout=10, verify=False)
        return r.json().get("access_token")
    except Exception as e:
        print(f"❌ Token Error: {e}")
        return None

def get_instance_count(url, uid, headers=None):
    """ითვლის სურათების რაოდენობას"""
    try:
        r = requests.get(f"{url}/studies/{uid}/instances", headers=headers, timeout=15, verify=False)
        if r.status_code == 200:
            return len(r.json())
        return 0
    except:
        return 0

async def monitor_study_progress(uid, name, total_count):
    """პროგრესის მონიტორინგი"""
    while True:
        done_count = get_instance_count(NEW_PACS_RS, uid)
        
        try:
            with open(ACTIVE_JSON, "r") as f:
                data = json.load(f)
        except:
            data = {}
        
        percent = int((done_count / total_count * 100)) if total_count > 0 else 0
        data[uid] = {"name": name, "done": done_count, "total": total_count, "percent": percent}
        
        with open(ACTIVE_JSON, "w") as f:
            json.dump(data, f)
        
        if done_count >= total_count or percent >= 100:
            break
        
        await asyncio.sleep(5)

async def transfer_study(uid, name, headers):
    """ერთი კვლევის გადატანა"""
    async with semaphore:
        print(f"📡 ვიწყებ: {name}")
        
        total_old = get_instance_count(OLD_PACS_RS, uid, headers)
        if total_old == 0:
            return

        export_url = f"{OLD_PACS_RS}/studies/{uid}/export/dicom:{AE_TITLE}"
        try:
            res = requests.post(export_url, headers=headers, verify=False, timeout=20)
            if res.status_code in [200, 201, 202]:
                await monitor_study_progress(uid, name, total_old)
                
                with open(PROGRESS_FILE, "a") as f:
                    f.write(uid + "\n")
                
                try:
                    with open(ACTIVE_JSON, "r") as f:
                        data = json.load(f)
                    if uid in data:
                        del data[uid]
                    with open(ACTIVE_JSON, "w") as f:
                        json.dump(data, f)
                except:
                    pass
                print(f"✅ დასრულდა: {name}")
            else:
                print(f"❌ ექსპორტზე უარი: {uid}")
        except Exception as e:
            print(f"❌ შეცდომა: {e}")

# ==========================================
# 3. მთავარი რობოტი
# ==========================================

async def run_robot():
    print("🤖 Migration Robot is standing by...")
    while True:
        try:
            # 1. სტატუსის შემოწმება
            mode = "stopped"
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, "r") as f:
                    mode = f.read().strip()
            
            now = datetime.datetime.now()
            is_night = (now.hour >= START_HOUR and now.hour < END_HOUR)
            
            if mode == "active" and is_night:
                token = get_token()
                if token:
                    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                    
                    migrated = set()
                    if os.path.exists(PROGRESS_FILE):
                        with open(PROGRESS_FILE, "r") as f:
                            migrated = set(line.strip() for line in f)

                    r = requests.get(f"{OLD_PACS_RS}/studies?limit=20", headers=headers, verify=False, timeout=20)
                    if r.status_code == 200:
                        for s in r.json():
                            if datetime.datetime.now().hour >= END_HOUR:
                                break
                            
                            uid = s["0020000D"]["Value"][0]
                            name_val = s.get("00100010", {}).get("Value", [{}])[0]
                            name = name_val.get("Alphabetic", "Unknown")

                            if uid not in migrated:
                                asyncio.create_task(transfer_study(uid, name, headers))
            
            elif mode == "active" and not is_night:
                # დღისით ვისვენებთ
                pass

        except Exception as e:
            print(f"❌ Loop Error: {e}")
        
        await asyncio.sleep(60)

if __name__ == "__main__":
    if not os.path.exists(ACTIVE_JSON):
        with open(ACTIVE_JSON, "w") as f:
            json.dump({}, f)
    asyncio.run(run_robot())