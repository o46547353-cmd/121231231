### slash_vpn_bot/web_app/main.py
import os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import storage
import threads_api
import ai_gen

app = FastAPI(title="SLASH VPN Bot Panel")

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Static files (опционально)
static_dir = os.path.join(BASE_DIR, "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
async def startup():
    threads_api.load_accounts_from_db()


# --- Главная ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    all_logins = storage.get_all_accounts()
    accounts = [storage.get_account(a['login']) for a in all_logins]
    accounts = [a for a in accounts if a]
    for acc in accounts:
        acc['has_image'] = bool(storage.get_image(acc['login']))
        acc['in_cache'] = acc['login'] in threads_api.list_accounts()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "accounts": accounts,
        "queue": storage.get_queue(),
        "archive": storage.get_archive(10),
        "queue_count": storage.count(),
        "interval": storage.get_setting('interval_hours', '4'),
    })


# --- Добавление аккаунта (Selenium) ---
@app.post("/add_account")
async def add_account(login: str = Form(...), password: str = Form(...)):
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, threads_api.add_account, login, password)
        return RedirectResponse("/?msg=ok", status_code=303)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- Добавление аккаунта вручную ---
@app.post("/add_account_manual")
async def add_account_manual(
    login: str = Form(...),
    session_id: str = Form(...),
    csrf_token: str = Form(...)
):
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, threads_api.add_account_manual, login, session_id, csrf_token)
        return RedirectResponse("/?msg=ok", status_code=303)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- Промпты ---
@app.post("/set_prompts")
async def set_prompts(
    login: str = Form(...),
    account_prompt: str = Form(...),
    topic_prompt: str = Form(...)
):
    storage.add_account_prompt(login, account_prompt, topic_prompt)
    return RedirectResponse("/?msg=prompts_ok", status_code=303)


# --- Загрузка картинки ---
@app.post("/upload_image")
async def upload_image(login: str = Form(...), file: UploadFile = File(...)):
    os.makedirs('images', exist_ok=True)
    path = f"images/{login}.jpg"
    contents = await file.read()
    with open(path, 'wb') as f:
        f.write(contents)
    storage.set_image(login, path)
    return RedirectResponse("/?msg=image_ok", status_code=303)


# --- Генерация серии ---
@app.post("/generate_series")
async def generate_series(login: str = Form(...), topic: str = Form(...)):
    try:
        loop = asyncio.get_event_loop()
        series = await loop.run_in_executor(None, ai_gen.generate_series, topic, login)
        storage.add_series(series, login)
        return RedirectResponse("/?msg=series_ok", status_code=303)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- Автоматическая серия ---
@app.post("/auto_series")
async def auto_series(login: str = Form(...)):
    try:
        loop = asyncio.get_event_loop()
        topic = await loop.run_in_executor(None, ai_gen.generate_topic, login)
        series = await loop.run_in_executor(None, ai_gen.generate_series, topic, login)
        storage.add_series(series, login)
        return RedirectResponse("/?msg=auto_ok", status_code=303)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- Публикация из очереди ---
@app.post("/post_now")
async def post_now():
    try:
        item = storage.pop()
        if not item:
            return JSONResponse({"error": "Очередь пуста"}, status_code=400)
        image = storage.get_image(item['account_login'])
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, threads_api.post_series, item['posts'], image, item['account_login'])
        storage.archive_item(item['posts'], item['account_login'])
        return RedirectResponse("/?msg=posted", status_code=303)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --- Удаление из очереди ---
@app.post("/delete_queue_item")
async def delete_queue_item(item_id: int = Form(...)):
    storage.delete_queue_item(item_id)
    return RedirectResponse("/?msg=deleted", status_code=303)


# --- Настройка интервала ---
@app.post("/set_interval")
async def set_interval(interval_hours: int = Form(...)):
    if interval_hours < 1:
        return JSONResponse({"error": "Минимум 1 час"}, status_code=400)
    storage.set_setting('interval_hours', interval_hours)
    return RedirectResponse("/?msg=interval_ok", status_code=303)


# --- API: статус ---
@app.get("/api/status")
async def api_status():
    return {
        "accounts": threads_api.list_accounts(),
        "queue_count": storage.count(),
        "interval_hours": storage.get_setting('interval_hours', '4'),
    }
