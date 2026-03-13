### slash_vpn_bot/threads_auth.py
"""
Авторизация в Threads/Instagram через мобильный API (реверс-инжиниринг).
Без Selenium, без браузера — чистые requests.

Поток:
  1. fetch_headers()  → csrftoken, mid
  2. get_enc_key()    → key_id, pub_key (RSA)
  3. encrypt_password() → зашифрованный пароль
  4. login()          → sessionid, csrftoken, user_id
"""

import os, time, uuid, json, base64, struct, hashlib, hmac, logging
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.padding import OAEP, MGF1
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_der_public_key

logger = logging.getLogger(__name__)

# --- Константы устройства (эмулируем конкретный Android-девайс) ---
IG_APP_ID      = "567067343352427"
IG_APP_VERSION = "289.0.0.77.109"
DEVICE_ID      = str(uuid.uuid4())   # генерируется раз, можно хардкодить
PHONE_ID       = str(uuid.uuid4())
UUID_          = str(uuid.uuid4())

BASE_HEADERS = {
    "User-Agent": f"Instagram {IG_APP_VERSION} Android (29/10; 420dpi; 1080x1920; Xiaomi; Mi 9; cepheus; qcom; ru_RU; {IG_APP_ID})",
    "X-IG-App-ID": IG_APP_ID,
    "X-IG-Android-ID": f"android-{DEVICE_ID[:16]}",
    "X-IG-Device-ID": DEVICE_ID,
    "X-IG-Phone-ID": PHONE_ID,
    "X-Pigeon-Session-Id": str(uuid.uuid4()),
    "X-Ads-Opt-Out": "0",
    "X-Google-AD-ID": str(uuid.uuid4()),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "*/*",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def _ig_signature(data: str) -> str:
    """HMAC-SHA256 подпись данных (ключ захардкожен в APK Instagram)."""
    IG_SIG_KEY = "4f8732eb9ba7d1c8e8897a75d6474d4eb3f5279137431b2aafb71fafe2abe178"
    sig = hmac.new(IG_SIG_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"SIGNATURE.{sig}.{data}"


def fetch_headers(session: requests.Session) -> tuple[str, str]:
    """
    Шаг 1: получаем csrftoken и X-MID.
    Возвращает (csrftoken, mid).
    """
    r = session.get(
        "https://i.instagram.com/api/v1/si/fetch_headers/",
        headers={**BASE_HEADERS, "X-DEVICE-ID": UUID_},
        params={"challenge_type": "signup", "guid": UUID_},
        timeout=15
    )
    csrf = r.cookies.get("csrftoken", "")
    mid  = r.headers.get("X-MID", "")
    if not csrf:
        # Fallback: берём из Set-Cookie
        for c in r.headers.get("Set-Cookie", "").split(";"):
            if "csrftoken=" in c:
                csrf = c.split("csrftoken=")[1].strip()
                break
    logger.debug(f"fetch_headers: csrf={csrf[:10]}... mid={mid[:10]}...")
    return csrf, mid


def get_enc_key(session: requests.Session, csrf: str, mid: str) -> tuple[int, int, str]:
    """
    Шаг 2: получаем публичный ключ для шифрования пароля.
    Возвращает (key_version, key_id, public_key_pem).
    """
    r = session.get(
        "https://i.instagram.com/api/v1/qe/sync/",
        headers={
            **BASE_HEADERS,
            "X-CSRFToken": csrf,
            "X-MID": mid,
        },
        timeout=15
    )
    # Ключ в заголовке ответа: #PWD_INSTAGRAM:4:<key_id>:<key_version>:<pubkey_b64>
    enc_header = r.headers.get("ig-set-password-encryption-key-id", "")
    enc_version = r.headers.get("ig-set-password-encryption-pub-key", "")

    if not enc_header or not enc_version:
        # Альтернативный эндпоинт
        r2 = session.post(
            "https://i.instagram.com/api/v1/accounts/get_password_encryption_keyset/",
            headers={**BASE_HEADERS, "X-CSRFToken": csrf, "X-MID": mid},
            timeout=15
        )
        try:
            d = r2.json()
            key_id      = int(d.get("key_id", 0))
            key_version = int(d.get("public_key_id", 0) or d.get("key_version", 0))
            pub_key_b64 = d.get("public_key", "")
            return key_version, key_id, pub_key_b64
        except Exception as e:
            raise Exception(f"Не удалось получить ключ шифрования: {e} | {r2.text[:200]}")

    key_id      = int(enc_header.strip())
    key_version = int(r.headers.get("ig-set-password-encryption-key-version", "0").strip())
    pub_key_b64 = enc_version.strip()
    logger.debug(f"enc key: id={key_id} version={key_version}")
    return key_version, key_id, pub_key_b64


def encrypt_password(password: str, key_id: int, key_version: int, pub_key_b64: str) -> str:
    """
    Шаг 3: шифруем пароль по схеме Instagram.

    Формат зашифрованного пароля (бинарный, потом base64):
      [0x01][key_version: 1 byte][key_id: 2 bytes LE]
      [iv: 12 bytes]
      [encrypted_aes_key: 256 bytes]  ← AES-ключ, зашифрованный RSA-OAEP
      [auth_tag: 16 bytes]            ← из AES-GCM
      [encrypted_password: N bytes]
    """
    # Генерируем случайный AES-256 ключ и IV
    aes_key = os.urandom(32)
    iv      = os.urandom(12)

    # Загружаем RSA публичный ключ
    pub_key_der = base64.b64decode(pub_key_b64)
    pub_key     = load_der_public_key(pub_key_der)

    # Шифруем AES-ключ через RSA-OAEP-SHA256
    encrypted_aes_key = pub_key.encrypt(
        aes_key,
        OAEP(mgf=MGF1(algorithm=SHA256()), algorithm=SHA256(), label=None)
    )  # 256 байт для RSA-2048

    # Шифруем пароль через AES-256-GCM
    # Additional data = unix timestamp в виде строки
    timestamp = str(int(time.time()))
    aesgcm = AESGCM(aes_key)
    encrypted_with_tag = aesgcm.encrypt(iv, password.encode(), timestamp.encode())

    # AES-GCM возвращает ciphertext + tag (последние 16 байт)
    encrypted_password = encrypted_with_tag[:-16]
    auth_tag           = encrypted_with_tag[-16:]

    # Сборка бинарного payload
    payload = (
        b"\x01"
        + struct.pack("<B", key_version)
        + struct.pack("<H", key_id)
        + iv
        + struct.pack("<H", len(encrypted_aes_key))
        + encrypted_aes_key
        + auth_tag
        + encrypted_password
    )

    enc_b64 = base64.b64encode(payload).decode()
    # Итоговая строка для поля enc_password
    return f"#PWD_INSTAGRAM:4:{timestamp}:{enc_b64}"


def login(username: str, password: str) -> dict:
    """
    Полный цикл авторизации через мобильный API Instagram/Threads.

    Возвращает dict:
      {session_id, csrf_token, user_id, username, session}
    или бросает Exception с понятным сообщением.
    """
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    # Шаг 1
    csrf, mid = fetch_headers(session)

    # Шаг 2
    key_version, key_id, pub_key_b64 = get_enc_key(session, csrf, mid)

    # Шаг 3
    enc_password = encrypt_password(password, key_id, key_version, pub_key_b64)

    # Шаг 4: POST login
    login_data = {
        "username":          username,
        "enc_password":      enc_password,
        "device_id":         DEVICE_ID,
        "guid":              UUID_,
        "phone_id":          PHONE_ID,
        "login_attempt_count": "0",
    }

    r = session.post(
        "https://i.instagram.com/api/v1/accounts/login/",
        data=login_data,
        headers={
            **BASE_HEADERS,
            "X-CSRFToken": csrf,
            "X-MID": mid,
        },
        timeout=20
    )

    try:
        data = r.json()
    except Exception:
        raise Exception(f"Сервер вернул не JSON: {r.status_code} {r.text[:200]}")

    # Обработка ошибок
    if r.status_code == 400:
        msg = data.get("message", "")
        error = data.get("error_type", "")
        if "bad_password" in error or "Invalid" in msg:
            raise Exception("Неверный логин или пароль")
        if "checkpoint" in error or "challenge" in str(data):
            raise Exception("Требуется подтверждение входа (checkpoint). Войди вручную в браузере и добавь аккаунт через /manual_cookies")
        if "two_factor" in error or data.get("two_factor_required"):
            raise Exception("Включена двухфакторная аутентификация. Отключи 2FA или используй /manual_cookies")
        raise Exception(f"Ошибка входа: {msg or error or str(data)[:200]}")

    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")

    if "logged_in_user" not in data:
        raise Exception(f"Неожиданный ответ: {str(data)[:200]}")

    user      = data["logged_in_user"]
    user_id   = str(user.get("pk") or user.get("id", ""))
    uname     = user.get("username", username)
    session_id = session.cookies.get("sessionid", "")
    csrf_out   = session.cookies.get("csrftoken", csrf)

    if not session_id:
        raise Exception("Авторизация прошла, но sessionid не получен — возможно, аккаунт заблокирован")

    logger.info(f"[{uname}] Авторизован. user_id={user_id}")

    return {
        "session_id":  session_id,
        "csrf_token":  csrf_out,
        "user_id":     user_id,
        "username":    uname,
        "session":     session,   # сохраняем сессию для дальнейших запросов
    }
