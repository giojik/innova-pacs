import os, psycopg2, datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

def get_db_stats(days=1):
    try:
        conn = psycopg2.connect(
            host="db",
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS")
        )
        cur = conn.cursor()
        query = f"""
            SELECT series.modality, COUNT(DISTINCT study.pk) 
            FROM study 
            JOIN series ON series.study_fk = study.pk 
            WHERE study.created_time > CURRENT_TIMESTAMP - INTERVAL '{days} days'
            GROUP BY series.modality;
        """
        cur.execute(query)
        data = cur.fetchall()
        cur.close()
        conn.close()
        return data
    except Exception as e:
        return [("Error", str(e))]

@app.get("/", response_class=HTMLResponse)
async def stats_page():
    periods = [("დღეს", 1), ("კვირა", 7), ("თვე", 30), ("წელი", 365)]
    content = ""
    for title, days in periods:
        data = get_db_stats(days)
        rows = "".join([f"<tr><td class='p-2 border font-bold text-blue-700'>{r[0]}</td><td class='p-2 border text-center'>{r[1]}</td></tr>" for r in data])
        content += f"""
        <div class='bg-white p-5 rounded-xl shadow'>
            <h3 class='font-bold text-gray-700 mb-3 border-bottom'>{title}</h3>
            <table class='w-full text-sm'>{rows}</table>
        </div>"""

    return f"""
    <html><head><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-100 p-8">
        <h2 class="text-2xl font-bold mb-6 text-slate-800">📊 PACS მოდალობების სტატისტიკა</h2>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4">{content}</div>
        <div class="mt-10"><a href="/manage/dashboard" class="text-blue-600">⬅️ უკან</a></div>
    </body></html>"""