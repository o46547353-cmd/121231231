### slash_vpn_bot/threads_api.py
"""
Threads API: авторизация через Selenium (реальные селекторы Threads/Instagram),
публикация постов через неофициальный мобильный API.
Аккаунты персистируются в SQLite через storage.py.
"""
import os, time, json, random, uuid, requests, logging
from dotenv import load_dotenv
import storage
import threads_auth

load_dotenv()
logger = logging.getLogger(__name__)

# Кэш аккаунтов в памяти: login -> dict с куками
_accounts_cache: dict = {}



def load_accounts_from_db():
    """Загружает все аккаунты из БД в кэш при старте."""
    for acc_ref in storage.get_all_accounts():
        acc = storage.get_account(acc_ref['login'])
        if acc and acc.get('session_id'):
            _accounts_cache[acc['login']] = {
                'SESSION_ID': acc['session_id'],
                'CSRF_TOKEN': acc['csrf_token'],
                'USERNAME': acc['username'],
                'USER_ID': acc['user_id'],
                'LOGIN': acc['login'],
            }
    logger.info(f"Загружено аккаунтов из БД: {len(_accounts_cache)}")


def add_account(login: str, password: str) -> dict:
    """
    Авторизация через мобильный API Instagram (реверс-инжиниринг).
    Без Selenium, без браузера.
    """
    logger.info(f"[{login}] Авторизация через мобильный API...")
    result = threads_auth.login(login, password)

    account_data = {
        'SESSION_ID': result['session_id'],
        'CSRF_TOKEN':  result['csrf_token'],
        'USERNAME':    result['username'],
        'USER_ID':     result['user_id'],
        'LOGIN':       login,
    }
    _accounts_cache[login] = account_data

    storage.save_account({
        'login':      login,
        'session_id': result['session_id'],
        'csrf_token': result['csrf_token'],
        'user_id':    result['user_id'],
        'username':   result['username'],
    })

    logger.info(f"[{login}] Авторизован успешно. user_id={result['user_id']}")
    return account_data


def add_account_manual(login: str, session_id: str, csrf_token: str) -> dict:
    """
    Добавление аккаунта вручную через cookies из браузера.
    Полезно если Selenium ломается.
    """
    account_data = {
        'SESSION_ID': session_id,
        'CSRF_TOKEN': csrf_token,
        'USERNAME': login,
        'USER_ID': '',
        'LOGIN': login,
    }

    # Пробуем получить user_id через API
    try:
        r = requests.get(
            f'https://www.threads.net/api/v1/users/web_profile_info/?username={login}',
            headers=_get_headers(account_data),
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            account_data['USER_ID'] = str(data.get('data', {}).get('user', {}).get('id', ''))
            account_data['USERNAME'] = data.get('data', {}).get('user', {}).get('username', login)
    except Exception as e:
        logger.warning(f"[{login}] Не получили user_id: {e}")

    _accounts_cache[login] = account_data
    storage.save_account({
        'login': login,
        'session_id': session_id,
        'csrf_token': csrf_token,
        'user_id': account_data['USER_ID'],
        'username': account_data['USERNAME'],
    })
    return account_data


def get_account(login: str = None) -> dict:
    """Возвращает аккаунт из кэша по логину или первый доступный."""
    if login and login in _accounts_cache:
        return _accounts_cache[login]
    if not login and _accounts_cache:
        return next(iter(_accounts_cache.values()))
    raise Exception(f"Аккаунт {'«' + login + '»' if login else ''} не найден в кэше. Авторизуйтесь заново.")


def list_accounts() -> list:
    return list(_accounts_cache.keys())


def _get_headers(account: dict) -> dict:
    return {
        "User-Agent": "Barcelona 289.0.0.77.109 Android",
        "X-CSRFToken": account['CSRF_TOKEN'],
        "X-IG-App-ID": "238260118697367",
        "Cookie": f"sessionid={account['SESSION_ID']}; csrftoken={account['CSRF_TOKEN']}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.threads.com",
        "Referer": f"https://www.threads.com/@{account.get('USERNAME', '')}",
    }


def _upload_image(account: dict, image_path: str) -> str:
    """Загружает картинку и возвращает upload_id."""
    upload_id = str(int(time.time() * 1000))
    url = f"https://www.threads.com/rupload_igphoto/fb_uploader_{upload_id}"

    with open(image_path, 'rb') as f:
        img_data = f.read()

    rupload_params = json.dumps({
        "upload_id": upload_id,
        "media_type": 1,
        "image_compression": {"lib_name": "moz", "lib_version": "3.1.m", "quality": "87"}
    })

    headers = _get_headers(account)
    headers.update({
        "Content-Type": "application/octet-stream",
        "X-Entity-Length": str(len(img_data)),
        "X-Entity-Name": f"fb_uploader_{upload_id}",
        "X-Instagram-Rupload-Params": rupload_params,
        "offset": "0"
    })

    r = requests.post(url, headers=headers, data=img_data, timeout=30)
    if r.status_code != 200:
        raise Exception(f"Ошибка загрузки картинки: {r.status_code} {r.text[:300]}")

    logger.info(f"Картинка загружена, upload_id={upload_id}. Жду 10 сек...")
    time.sleep(10)
    return upload_id


def _post_single(account: dict, text: str, reply_to_id: str = None, image_path: str = None) -> str:
    """Публикует один пост. Возвращает pk (ID поста)."""
    upload_id = None
    has_image = False

    if image_path and os.path.exists(image_path):
        upload_id = _upload_image(account, image_path)
        has_image = True
    elif image_path:
        logger.warning(f"Картинка не найдена: {image_path}, публикуем без неё")

    app_info = {
        "entry_point": "create_reply" if reply_to_id else "sidebar_navigation",
        "reply_control": 0,
        "self_thread_context_id": str(uuid.uuid4()),
    }
    if reply_to_id:
        app_info["reply_id"] = str(reply_to_id)

    payload = {
        "caption": text,
        "text_post_app_info": json.dumps(app_info),
        "upload_id": upload_id or str(int(time.time() * 1000)),
        "is_threads": "true",
    }
    if reply_to_id:
        payload["barcelona_source_reply_id"] = str(reply_to_id)

    if has_image:
        url = "https://www.threads.com/api/v1/media/configure_text_post_app_feed/"
    else:
        url = "https://www.threads.com/api/v1/media/configure_text_only_post/"

    r = requests.post(url, headers=_get_headers(account), data=payload, timeout=30)

    if r.status_code == 400:
        raise Exception(f"Ошибка публикации 400 (плохой запрос): {r.text[:300]}")
    if r.status_code == 401:
        raise Exception(f"Сессия протухла для аккаунта {account.get('LOGIN')}. Нужна переавторизация.")
    if r.status_code != 200:
        raise Exception(f"Ошибка публикации {r.status_code}: {r.text[:300]}")

    d = r.json()
    pk = d.get("media", {}).get("pk") or d.get("pk")
    if not pk:
        raise Exception(f"Не получен pk поста. Ответ: {str(d)[:200]}")

    logger.info(f"Пост опубликован, pk={pk}")
    return str(pk)


def post_series(posts: dict, image_path: str = None, account_login: str = None) -> list:
    """
    Публикует серию из 4 постов цепочкой (reply-цепочка).
    posts: dict с ключами post1, post2, post3, post4
    """
    account = get_account(account_login)
    ids = []

    logger.info(f"[{account['LOGIN']}] Публикую серию: {posts.get('topic', '—')}")

    id1 = _post_single(account, posts['post1'])
    ids.append(id1)
    time.sleep(random.uniform(8, 14))

    id2 = _post_single(account, posts['post2'], reply_to_id=id1)
    ids.append(id2)
    time.sleep(random.uniform(8, 14))

    id3 = _post_single(account, posts['post3'], reply_to_id=id2, image_path=image_path)
    ids.append(id3)
    time.sleep(random.uniform(8, 14))

    id4 = _post_single(account, posts['post4'], reply_to_id=id3)
    ids.append(id4)

    logger.info(f"[{account['LOGIN']}] Серия опубликована: {ids}")
    return ids


def post_single_text(text: str, account_login: str = None) -> str:
    """Публикует отдельный текстовый пост."""
    account = get_account(account_login)
    return _post_single(account, text)
