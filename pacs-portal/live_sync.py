import os, requests, asyncio, datetime, json, urllib3
from fastapi import FastAPI

urllib3.disable_warnings()
app_active_json = "/app/live_sync_active.json"
status_file = "/app/live_sync_status.txt"

# კონფიგურაცია
AE_TITLE = os.getenv("AE_TITLE", "RISINNOVA")
OLD_PACS_RS = os.getenv("OLD_PACS_URL")
NEW_PACS_RS = f"http://pacs-arc:8080/dcm4chee-arc/aets/{AE_TITLE}/rs"
OLD_TOKEN_URL = os.getenv("OLD_KEYCLOAK_URL")
OLD_CLIENT_ID = os.getenv("OLD_CLIENT_ID")
OLD_CLIENT_SECRET = os.getenv("OLD_CLIENT_SECRET")

semaphore = asyncio.Semaphore(3)

def get_token():
    try:
        data = {"client_id": OLD_CLIENT_ID, "client_secret": OLD_CLIENT_SECRET, "grant_type": "client_credentials"}
        r = requests.post(OLD_TOKEN_URL, data=data, timeout=10, verify=False)
        return r.json().get("access_token") if r.status_code == 200 else None
    except: return None

def get_count(url, uid, headers=None):
    try:
        r = requests.get(f"{url}/studies/{uid}/instances", headers=headers, timeout=10, verify=False)
        return len(r.json()) if r.status_code == 200 else 0
    except: return 0

async def monitor_progress(uid, name, total):
    while True:
        try:
            done = get_count(NEW_PACS_RS, uid)
            percent = int((done / total * 100)) if total > 0 else 0
            
            with open(app_active_json, "r") as f:
                data = json.load(f)
            
            data[uid] = {"name": name, "done": done, "total": total, "percent": percent}
            
            with open(app_active_json, "w") as f:
                json.dump(data, f)
            
            if done >= total or percent >= 100:
                print(f"✅ Sync Finished: {name}")
                break
        except: pass
        await asyncio.sleep(3)

async def transfer_study(uid, name, headers):
    async with semaphore:
        try:
            total = get_count(OLD_PACS_RS, uid, headers)
            if total == 0: return

            # 1. მაშინვე ვწერთ JSON-ში რომ გამოჩნდეს პანელზე
            try:
                with open(app_active_json, "r") as f: data = json.load(f)
            except: data = {}
            data[uid] = {"name": name, "done": 0, "total": total, "percent": 0}
            with open(app_active_json, "w") as f: json.dump(data, f)

            print(f"📦 Starting transfer: {name} ({total} images)")
            
            # 2. ექსპორტის ბრძანება
            requests.post(f"{OLD_PACS_RS}/studies/{uid}/export/dicom:{AE_TITLE}", headers=headers, verify=False, timeout=15)
            
            # 3. მონიტორინგი
            await monitor_progress(uid, name, total)
            
            # 4. გაწმენდა
            with open(app_active_json, "r") as f: data = json.load(f)
            if uid in data: del data[uid]
            with open(app_active_json, "w") as f: json.dump(data, f)
        except Exception as e:
            print(f"❌ Transfer Error {name}: {e}")

async def sync_engine():
    print("🚀 Sync Engine is searching for studies...")
    while True:
        try:
            mode = "active"
            if os.path.exists(status_file):
                with open(status_file, "r") as f: mode = f.read().strip()
            
            if mode == "active":
                token = get_token()
                if token:
                    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                    # ვეძებთ ბოლო 3 დღის კვლევებს
                    today = datetime.datetime.now().strftime("%Y%m%d")
                    three_days_ago = (datetime.datetime.now() - datetime.timedelta(days=3)).strftime("%Y%m%d")
                    
                    r = requests.get(f"{OLD_PACS_RS}/studies?StudyDate={three_days_ago}-{today}", headers=headers, verify=False, timeout=15)
                    
                    if r.status_code == 200:
                        studies = r.json()
                        print(f"🔎 Found {len(studies)} studies in last 3 days.")
                        
                        for s in studies:
                            uid = s["0020000D"]["Value"][0]
                            name = s.get("00100010", {}).get("Value", [{}])[0].get("Alphabetic", "Unknown")
                            
                            # შედარება
                            total_old = get_count(OLD_PACS_RS, uid, headers)
                            done_new = get_count(NEW_PACS_RS, uid)
                            
                            if done_new < total_old:
                                # მხოლოდ მაშინ ვუშვებთ, თუ უკვე არ არის აქტიურებში
                                try:
                                    with open(app_active_json, "r") as f: active_data = json.load(f)
                                except: active_data = {}
                                
                                if uid not in active_data:
                                    asyncio.create_task(transfer_study(uid, name, headers))
        except Exception as e:
            print(f"⚠️ Engine Error: {e}")
            
        await asyncio.sleep(60)

app = FastAPI()
@app.on_event("startup")
async def startup():
    # JSON-ის გასუფთავება სტარტზე
    with open(app_active_json, "w") as f: json.dump({}, f)
    asyncio.create_task(sync_engine())