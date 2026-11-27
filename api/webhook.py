# Бот «Как обстановка?» для Vercel (без постоянно запущенного сервера)
# Положите этот файл в репозиторий GitHub по пути: api/webhook.py
# В настройках проекта на Vercel добавьте переменную окружения BOT_TOKEN.
# Для автоудаления старых вопросов подключите в Vercel: Storage → Upstash (Redis).
# Никаких дополнительных библиотек не нужно.

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

TOKEN = os.environ.get("BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"

# Старые вопросы удаляются через столько секунд после того, как задан новый вопрос
DELETE_AFTER = 30 * 60  # 30 хвилин

# Скільки питань максимум може бути в чаті одночасно
MAX_POLLS = 2

# «Как обстановка?» можно спрашивать не чаще, чем раз в столько секунд
ASK_COOLDOWN = 5 * 60  # 5 минут

QUESTION = "Як обстановка?"
OPTIONS = [
    "Тихо",
    "Берізки + Фізтех",
    "Бульвар + Школи",
    "Церква + Шосе",
    "Інститут + Сквер",
    "Гаражі «Нейтрон»",
    "Гаражі «Берізки»",
    "БАМ",
    "На Жуки",
    "На Олексіївку",
]

# Напрямки, куди полетів (сині кнопки)
YELLOW = {"На Жуки", "На Олексіївку"}


def mark_of(i):
    if i == 0:
        return "🟢"
    return "🔵" if OPTIONS[i] in YELLOW else "🔴"
# Стара кнопка (російською) теж спрацює, поки у всіх не оновиться клавіатура
QUESTION_ALIASES = {QUESTION, "Как обстановка?"}

# Постоянная кнопка внизу экрана
MAIN_KB = {
    "keyboard": [[{"text": QUESTION}]],
    "resize_keyboard": True,
    "is_persistent": True,
}

# Кнопки ответа под вопросом
# «Тихо» — всегда зелёная кнопка, остальные — всегда красные.
def answers_kb(answers=None):
    # Тихо — зелена, місця — червоні, напрямки — сині
    btns = []
    for i, opt in enumerate(OPTIONS):
        style = "success" if i == 0 else ("primary" if opt in YELLOW else "danger")
        btns.append({"text": opt, "callback_data": f"v:{i}", "style": style})
    red = [b for b, o in zip(btns, OPTIONS) if o not in YELLOW][1:]
    yellow = [b for b, o in zip(btns, OPTIONS) if o in YELLOW]
    # «Тихо» — окремим рядком, червоні — по два в рядку, сині — останнім рядком
    rows = [[btns[0]]] + [red[i:i + 2] for i in range(0, len(red), 2)] + [yellow]
    return {"inline_keyboard": rows}


def tg(method, data):
    req = urllib.request.Request(
        API + method,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as e:
        print("Telegram error:", method, e)
        return {}


# ---------- Хранилище (Upstash Redis), чтобы помнить отправленные вопросы ----------
REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


def redis(*cmd):
    if not REDIS_URL or not REDIS_TOKEN:
        return None
    req = urllib.request.Request(
        REDIS_URL,
        data=json.dumps([str(c) for c in cmd]).encode("utf-8"),
        headers={"Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read()).get("result")
    except Exception as e:
        print("Redis error:", cmd[0], e)
        return None


def remember_poll(chat_id, message_id):
    redis("ZADD", f"polls:{chat_id}", int(time.time()), message_id)
    redis("SADD", "chats", chat_id)


def try_start_cooldown(chat_id):
    """True — можно задавать вопрос (и таймер 5 минут запущен). Иначе — сколько секунд ждать."""
    ok = redis("SET", f"cooldown:{chat_id}", int(time.time()), "NX", "EX", ASK_COOLDOWN)
    if ok is None and REDIS_URL:
        ttl = redis("TTL", f"cooldown:{chat_id}")
        return max(int(ttl or 0), 1)
    return True


def cleanup(chat_id):
    """Удаляет вопросы, после которых уже больше часа назад был задан новый вопрос."""
    # Служебные сообщения («подождите…») удаляем, когда подошло их время
    trash = redis("ZRANGEBYSCORE", f"trash:{chat_id}", 0, int(time.time())) or []
    for mid in trash:
        tg("deleteMessage", {"chat_id": chat_id, "message_id": int(mid)})
    if trash:
        redis("ZREM", f"trash:{chat_id}", *trash)

    rows = redis("ZRANGE", f"polls:{chat_id}", 0, -1, "WITHSCORES")
    if not rows:
        return
    polls = [(rows[i], float(rows[i + 1])) for i in range(0, len(rows), 2)]  # по времени
    limit = time.time() - DELETE_AFTER
    # Найновіше питання, поставлене більше 30 хв тому: усе, що раніше нього, — видаляємо
    cutoff = None
    for _, ts in polls:
        if ts <= limit:
            cutoff = ts
    old = [mid for mid, ts in polls if cutoff is not None and ts < cutoff]
    # І в чаті не більше MAX_POLLS питань: найстаріші зайві — одразу
    rest = [mid for mid, _ in polls if mid not in old]
    old += rest[:-MAX_POLLS] if len(rest) > MAX_POLLS else []
    for mid in old:
        tg("deleteMessage", {"chat_id": chat_id, "message_id": int(mid)})
    if old:
        redis("ZREM", f"polls:{chat_id}", *old)


def cleanup_all():
    for chat_id in redis("SMEMBERS", "chats") or []:
        cleanup(chat_id)


def now_kyiv():
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Kyiv")
    except Exception:
        tz = timezone(timedelta(hours=2))
    return datetime.now(tz).strftime("%H:%M")


def full_name(user):
    name = " ".join(p for p in [user.get("first_name"), user.get("last_name")] if p)
    return (name or "Без імені").replace(",", " ").replace("\n", " ").strip()


def render(header, answers):
    """answers: {ім'я: {номери обраних варіантів}}"""
    blocks = [header]
    # Показуємо лише варіанти, на які хтось відповів — так повідомлення коротше
    for i, opt in enumerate(OPTIONS):
        names = [n for n, picked in answers.items() if i in picked]
        if names:
            mark = mark_of(i)
            blocks.append(f"{mark} {opt} — {len(names)}\n" + ", ".join(names))
    if not answers:
        blocks.append("Поки ніхто не відповів")
    return "\n\n".join(blocks)


def parse(text):
    """Восстанавливает ответы из текста сообщения — так боту не нужна база данных."""
    blocks = text.split("\n\n")
    header = blocks[0]
    answers = {}
    for block in blocks[1:]:
        lines = block.split("\n")
        title = lines[0].rsplit(" — ", 1)[0]
        for mark in ("🟢 ", "🔴 ", "🟡 ", "🔵 "):
            if title.startswith(mark):
                title = title[len(mark):]
        if title in OPTIONS and len(lines) > 1:
            idx = OPTIONS.index(title)
            for name in lines[1].split(", "):
                if name:
                    answers.setdefault(name, set()).add(idx)
    return header, answers


def choose(picked, idx):
    """Одна людина: або «Тихо», або одне місце + один напрямок.
    Повторне натискання на вже обраний варіант знімає його."""
    picked = set(picked)
    if idx in picked:
        picked.discard(idx)
        return picked
    if idx == 0:
        return {0}
    picked.discard(0)
    same_group = YELLOW if OPTIONS[idx] in YELLOW else set(OPTIONS[1:]) - YELLOW
    picked = {i for i in picked if OPTIONS[i] not in same_group}
    picked.add(idx)
    return picked


def process(update):
    msg = update.get("message")
    # Прибираємо службові повідомлення «X приєднався / вийшов / додав Y»
    if msg and ("new_chat_members" in msg or "left_chat_member" in msg):
        tg("deleteMessage", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]})
        return
    if msg and "text" in msg:
        text = msg["text"].strip()
        chat_id = msg["chat"]["id"]
        if text.startswith("/start"):
            # Убираем команду /start из чата, оставляем только кнопку
            tg("deleteMessage", {"chat_id": chat_id, "message_id": msg["message_id"]})
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "👇",
                "reply_markup": MAIN_KB,
                "disable_notification": True,
            })
        elif text in QUESTION_ALIASES:
            # Убираем сообщение «Как обстановка?» от человека — в чате остаётся только опрос
            tg("deleteMessage", {"chat_id": chat_id, "message_id": msg["message_id"]})
            wait = try_start_cooldown(chat_id)
            if wait is not True:
                mins = (wait + 59) // 60
                note = tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"⏳ Нещодавно вже питали. Наступне питання можна поставити через {mins} хв.",
                    "disable_notification": True,
                })
                if note.get("ok"):
                    # Показуємо попередження 5 секунд і прибираємо
                    time.sleep(5)
                    tg("deleteMessage", {"chat_id": chat_id, "message_id": note["result"]["message_id"]})
                cleanup(chat_id)
                return
            header = f"❓ {QUESTION}\n{now_kyiv()}, питає {full_name(msg['from'])}"
            sent = tg("sendMessage", {
                "chat_id": chat_id,
                "text": render(header, {}),
                "reply_markup": answers_kb({}),
            })
            if sent.get("ok"):
                remember_poll(chat_id, sent["result"]["message_id"])
        cleanup(chat_id)
        return

    call = update.get("callback_query")
    if call and call.get("data", "").startswith("v:") and "message" in call:
        idx = int(call["data"].split(":")[1])
        message = call["message"]
        header, answers = parse(message.get("text", ""))
        name = full_name(call["from"])

        old = answers.get(name, set())
        new = choose(old, idx)
        if new:
            answers[name] = new
        else:
            answers.pop(name, None)
        if new != old:
            tg("editMessageText", {
                "chat_id": message["chat"]["id"],
                "message_id": message["message_id"],
                "text": render(header, answers),
                "reply_markup": answers_kb(answers),
            })
        tg("answerCallbackQuery", {
            "callback_query_id": call["id"],
            "text": ("Ваша відповідь: " + ", ".join(OPTIONS[i] for i in sorted(new))) if new
                    else "Відповідь знято",
        })
        cleanup(message["chat"]["id"])


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        try:
            update = json.loads(self.rfile.read(length) or b"{}")
            process(update)
        except Exception as e:
            print("Error:", e)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):
        # Адрес .../api/webhook?cleanup=1 — почистить старые вопросы во всех чатах
        if "cleanup" in self.path:
            cleanup_all()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Бот працює".encode("utf-8"))
