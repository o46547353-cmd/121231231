### slash_vpn_bot/ai_gen.py
import os, json, re, logging
from openai import OpenAI
from dotenv import load_dotenv
import storage

load_dotenv()
logger = logging.getLogger(__name__)

client = OpenAI(
    api_key=os.environ['AITUNNEL_API_KEY'],
    base_url='https://api.aitunnel.ru/v1/'
)

# --- Дефолтные системные промпты ---

DEFAULT_ACCOUNT_PROMPT = '''
Ты пишешь конверсионные посты для Threads о SLASH VPN.
Продукт: SLASH VPN — Telegram-бот для защиты трафика.
Тарифы: 1 день 10р, 3 дня 30р, 7 дней 70р, 14 дней 150р, 30 дней 199р.
CTA всегда: "напиши + в комментах — скину ссылку лично".
Тон: от первого лица, живой, без канцелярита, без клише.
Тарифы упоминать только в посте 3.

СТРОГО отвечай JSON без markdown, без пояснений, ТОЧНО в таком формате:
{
  "topic": "<тема серии>",
  "post1": "<текст хука>",
  "post2": "<текст боли>",
  "post3": "<текст решения с тарифами и CTA>",
  "post4": "<текст дожима с CTA>"
}
'''

DEFAULT_TOPIC_PROMPT = '''
Придумай одну свежую тему для рекламного поста о SLASH VPN в Threads.
SLASH VPN — Telegram-бот для защиты интернет-трафика. Тарифы от 10р/день.
Аудитория: обычные пользователи 18-35 лет, Россия.
Тема должна цеплять болью или страхом (слежка, блокировки, утечки данных, скорость).
Отвечай одной строкой, 3-8 слов, без кавычек и пояснений.
'''


def _get_prompts(account_login: str = None) -> tuple[str, str]:
    """Возвращает (account_prompt, topic_prompt) для аккаунта или дефолтные."""
    if account_login:
        acc = storage.get_account(account_login)
        if acc:
            ap = acc.get('account_prompt', '').strip()
            tp = acc.get('topic_prompt', '').strip()
            return (ap or DEFAULT_ACCOUNT_PROMPT), (tp or DEFAULT_TOPIC_PROMPT)
    return DEFAULT_ACCOUNT_PROMPT, DEFAULT_TOPIC_PROMPT


def generate_topic(account_login: str = None) -> str:
    _, topic_prompt = _get_prompts(account_login)
    resp = client.chat.completions.create(
        model='gpt-4.1-nano',
        messages=[
            {'role': 'system', 'content': topic_prompt},
            {'role': 'user', 'content': 'Придумай тему'}
        ],
        max_tokens=60,
        temperature=1.0
    )
    return resp.choices[0].message.content.strip().strip('"').strip("'")


def generate_series(topic: str, account_login: str = None) -> dict:
    """
    Генерирует серию из 4 постов по теме.
    Возвращает dict: {topic, post1, post2, post3, post4}
    """
    account_prompt, _ = _get_prompts(account_login)

    resp = client.chat.completions.create(
        model='gpt-4.1-nano',
        messages=[
            {'role': 'system', 'content': account_prompt},
            {'role': 'user', 'content': f'Тема: {topic}'}
        ],
        max_tokens=1400,
        temperature=0.85
    )

    text = resp.choices[0].message.content.strip()

    # Убираем markdown-обёртку если вдруг есть
    text = re.sub(r'```json\s*|```\s*', '', text).strip()

    # Исправляем переносы строк внутри JSON-строк
    def fix_newlines_in_strings(m):
        return m.group(0).replace('\n', '\\n').replace('\r', '')

    text = re.sub(r'"(?:[^"\\]|\\.)*"', fix_newlines_in_strings, text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nText: {text[:500]}")
        raise Exception(f"AI вернул невалидный JSON: {e}. Попробуй ещё раз.")

    # Проверяем структуру
    required = ['post1', 'post2', 'post3', 'post4']
    missing = [k for k in required if k not in data]
    if missing:
        raise Exception(f"AI не вернул поля: {missing}. Ответ: {str(data)[:200]}")

    # Добавляем тему если AI её не включил
    if 'topic' not in data:
        data['topic'] = topic

    return data
