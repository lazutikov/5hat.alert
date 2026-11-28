# Бот «Как обстановка?» для Vercel (без постоянно запущенного сервера)
# Положите этот файл в репозиторий GitHub по пути: api/webhook.py
# В настройках проекта на Vercel добавьте переменную окружения BOT_TOKEN.
# Для автоудаления старых вопросов подключите в Vercel: Storage → Upstash (Redis).
# Никаких дополнительных библиотек не нужно.

import json
import os
import sys
import time
import uuid
import urllib.request

# Карта поки вимкнена. Щоб увімкнути — постав True (і поклади файли api/_mapdraw.py, api/_map/)
USE_MAP = False

render_map = None
if USE_MAP:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from _mapdraw import render_map  # малює карту з зафарбованими зонами
    except Exception as e:  # якщо карта не завантажилась — бот працює текстом
        print("Map disabled:", e)
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

QUESTION = "Їду на район. Запитати про обстановку"
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
QUESTION_ALIASES = {QUESTION, "Іду на район. Запитати про обстановку", "Як обстановка?", "Как обстановка?"}

# Постоянная кнопка внизу экрана
# Друга кнопка внизу: повідомити, що чути ФПВ
FPV_BUTTON = "ФПВ на районі"

MAIN_KB = {
    "keyboard": [[{"text": QUESTION}, {"text": FPV_BUTTON}]],
    "resize_keyboard": True,
    "is_persistent": True,
}

# Кнопки ответа под вопросом
# «Тихо» — всегда зелёная кнопка, остальные — всегда красные.
def answers_kb(answers=None):
    # Без кольорової заливки (на iOS вона погано читається) — колір позначаємо значком
    btns = [{"text": f"{mark_of(i)} {opt}", "callback_data": f"v:{i}"}
            for i, opt in enumerate(OPTIONS)]
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


def tg_photo(method, data, photo_bytes):
    """Запит до Telegram з картинкою (multipart/form-data)."""
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode("utf-8")
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"map.jpg\"\r\n"
             f"Content-Type: image/jpeg\r\n\r\n").encode("utf-8") + photo_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(API + method, data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:
        print("Telegram error:", method, e)
        return {}


def counts_of(answers):
    counts = {}
    for picked in answers.values():
        for i in picked:
            counts[OPTIONS[i]] = counts.get(OPTIONS[i], 0) + 1
    return counts


def map_bytes(answers):
    if render_map is None:
        return None
    try:
        return render_map(counts_of(answers))
    except Exception as e:
        print("Map error:", e)
        return None


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
        return f"ПОМИЛКА: {e}" if cmd[0] == "PING" else None


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
        if str(mid).startswith("e:"):  # особисте (ефемерне) повідомлення: e:<user_id>:<id>
            _, uid, eid = str(mid).split(":")
            tg("deleteEphemeralMessage", {"chat_id": chat_id, "receiver_user_id": int(uid),
                                          "ephemeral_message_id": int(eid)})
        else:
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


WELCOME_TTL = 10 * 60  # привітання зникає само через 10 хвилин


def welcome_text(name):
    return (
        f"Вітаємо, {name}!\n\n"
        "Тут сусіди швидко повідомляють одне одному, чи літають ФПВ над нашим районом.\n\n"
        "Щоб запитати про обстановку:\n"
        f"Натисніть кнопку «{QUESTION}» внизу екрана. Якщо кнопки не видно, натисніть значок ⌨️ "
        "біля поля вводу. Бот поставить питання всім у групі, а відповіді з'являться під ним.\n\n"
        "Якщо чуєте ФПВ:\n"
        f"Натисніть «{FPV_BUTTON}» і оберіть, де саме чути. Бот одразу попередить усю групу."
    )


def fpv_picker_kb(user_id):
    """Вибір місця для «ФПВ на районі» — ті самі сектори, що й під питанням."""
    places = [(i, o) for i, o in enumerate(OPTIONS) if i != 0 and o not in YELLOW]
    btns = [{"text": f"🔴 {o}", "callback_data": f"f:{i}:{user_id}"} for i, o in places]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([{"text": "Скасувати", "callback_data": f"f:x:{user_id}"}])
    return {"inline_keyboard": rows}


def send_private(chat_id, user_id, text, **extra):
    """Повідомлення в групі, яке бачить лише одна людина («Бачите тільки ви»)."""
    sent = tg("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "ephemeral_message_parameters": {"receiver_user_id": user_id},
        **extra,
    })
    if sent.get("ok"):
        return sent["result"].get("ephemeral_message_id")
    return None


def send_welcome(chat_id, user):
    eid = send_private(chat_id, user["id"], welcome_text(full_name(user)),
                       reply_markup={"inline_keyboard": [[{"text": "Зрозуміло", "callback_data": "ok:"}]]})
    if eid:
        redis("ZADD", f"trash:{chat_id}", int(time.time()) + WELCOME_TTL, f"e:{user['id']}:{eid}")
        redis("SADD", "chats", chat_id)


def refresh_button(chat_id):
    """Надсилає свіжу «.» з кнопкою внизу і прибирає попередню «.»"""
    sent = tg("sendMessage", {
        "chat_id": chat_id,
        "text": ".",
        "reply_markup": MAIN_KB,
        "disable_notification": True,
    })
    if sent.get("ok"):
        old = redis("GETSET", f"kbmsg:{chat_id}", sent["result"]["message_id"])
        if old:
            tg("deleteMessage", {"chat_id": chat_id, "message_id": int(old)})


def is_admin(chat_id, msg):
    """Адміни (і анонімні адміни від імені групи) можуть писати текст."""
    if msg.get("sender_chat", {}).get("id") == chat_id:
        return True
    uid = msg.get("from", {}).get("id")
    if not uid:
        return False
    cached = redis("GET", f"adm:{chat_id}:{uid}")
    if cached is not None:
        return cached == "1"
    r = tg("getChatMember", {"chat_id": chat_id, "user_id": uid})
    ok = r.get("result", {}).get("status") in ("creator", "administrator")
    redis("SET", f"adm:{chat_id}:{uid}", "1" if ok else "0", "EX", 600)
    return ok


def is_bot_button(text):
    return text in QUESTION_ALIASES or text == FPV_BUTTON or text.startswith("/start")


def process(update):
    msg = update.get("message")
    # Прибираємо службові повідомлення «X приєднався / вийшов / додав Y»
    if msg and ("new_chat_members" in msg or "left_chat_member" in msg):
        tg("deleteMessage", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]})
        # Новим учасникам одразу показуємо кнопку
        if "new_chat_members" in msg:
            refresh_button(msg["chat"]["id"])
            for user in msg["new_chat_members"]:
                if not user.get("is_bot"):
                    send_welcome(msg["chat"]["id"], user)
        return
    # Учасникам писати текст не можна: усе, крім натискань кнопок, бот видаляє
    if msg and msg.get("chat", {}).get("type") in ("group", "supergroup"):
        if not is_bot_button((msg.get("text") or "").strip()) and not is_admin(msg["chat"]["id"], msg):
            tg("deleteMessage", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]})
            return
    if msg and "text" in msg:
        text = msg["text"].strip()
        chat_id = msg["chat"]["id"]
        if text.startswith("/start"):
            # Убираем команду /start из чата, оставляем только кнопку
            tg("deleteMessage", {"chat_id": chat_id, "message_id": msg["message_id"]})
            refresh_button(chat_id)
        elif text == FPV_BUTTON:
            # Прибираємо натискання і показуємо вибір місця лише цій людині
            tg("deleteMessage", {"chat_id": chat_id, "message_id": msg["message_id"]})
            uid = msg["from"]["id"]
            ask = "Де чути ФПВ? Оберіть місце:"
            if not send_private(chat_id, uid, ask, reply_markup=fpv_picker_kb(uid)):
                # Якщо Telegram не прийняв особисте повідомлення — показуємо звичайне
                tg("sendMessage", {"chat_id": chat_id, "text": f"{full_name(msg['from'])}, {ask[0].lower()}{ask[1:]}",
                                   "reply_markup": fpv_picker_kb(uid), "disable_notification": True})
        elif text in QUESTION_ALIASES:
            # Убираем сообщение «Как обстановка?» от человека — в чате остаётся только опрос
            tg("deleteMessage", {"chat_id": chat_id, "message_id": msg["message_id"]})
            wait = try_start_cooldown(chat_id)
            if wait is not True:
                mins = (wait + 59) // 60
                # Попередження бачить лише той, хто натиснув
                eid = send_private(chat_id, msg["from"]["id"],
                                   f"⏳ Нещодавно вже питали. Наступне питання можна поставити через {mins} хв.")
                if eid:
                    redis("ZADD", f"trash:{chat_id}", int(time.time()) + 60, f"e:{msg['from']['id']}:{eid}")
                    redis("SADD", "chats", chat_id)
                cleanup(chat_id)
                return
            header = f"{now_kyiv()} {full_name(msg['from'])} їде на район та питає про обстановку"
            pic = map_bytes({})
            if pic:
                sent = tg_photo("sendPhoto", {
                    "chat_id": chat_id,
                    "caption": render(header, {}),
                    "reply_markup": answers_kb({}),
                }, pic)
            else:
                sent = {}
            if not sent.get("ok"):
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
    # Вибір місця після «ФПВ на районі»
    if call and call.get("data", "").startswith("f:") and "message" in call:
        _, choice, owner = call["data"].split(":")
        if str(call["from"]["id"]) != owner:
            tg("answerCallbackQuery", {"callback_query_id": call["id"],
                                       "text": "Місце обирає той, хто чув ФПВ"})
            return
        m = call["message"]
        chat_id = m["chat"]["id"]
        if m.get("ephemeral_message_id"):
            tg("deleteEphemeralMessage", {"chat_id": chat_id, "receiver_user_id": call["from"]["id"],
                                          "ephemeral_message_id": m["ephemeral_message_id"]})
        else:
            tg("deleteMessage", {"chat_id": chat_id, "message_id": m["message_id"]})
        if choice == "x":
            tg("answerCallbackQuery", {"callback_query_id": call["id"], "text": "Скасовано"})
            return
        place = OPTIONS[int(choice)]
        tg("sendMessage", {"chat_id": chat_id, "text": f"‼️‼️‼️ ФПВ ({place}) {now_kyiv()}\nПовідомляє: {full_name(call['from'])}"})
        tg("answerCallbackQuery", {"callback_query_id": call["id"], "text": "Групу попереджено"})
        return
    # Кнопка «Зрозуміло» під привітанням
    if call and call.get("data", "").startswith("ok:") and "message" in call:
        m = call["message"]
        if m.get("ephemeral_message_id"):
            tg("deleteEphemeralMessage", {"chat_id": m["chat"]["id"], "receiver_user_id": call["from"]["id"],
                                          "ephemeral_message_id": m["ephemeral_message_id"]})
        else:
            tg("deleteMessage", {"chat_id": m["chat"]["id"], "message_id": m["message_id"]})
        tg("answerCallbackQuery", {"callback_query_id": call["id"]})
        return
    if call and call.get("data", "").startswith("v:") and "message" in call:
        idx = int(call["data"].split(":")[1])
        message = call["message"]
        is_photo = "photo" in message
        header, answers = parse(message.get("caption") or message.get("text") or "")
        name = full_name(call["from"])

        old = answers.get(name, set())
        new = choose(old, idx)
        if new:
            answers[name] = new
        else:
            answers.pop(name, None)
        if new != old:
            pic = map_bytes(answers) if is_photo else None
            if pic:
                tg_photo("editMessageMedia", {
                    "chat_id": message["chat"]["id"],
                    "message_id": message["message_id"],
                    "media": {"type": "photo", "media": "attach://photo",
                              "caption": render(header, answers)},
                    "reply_markup": answers_kb(answers),
                }, pic)
            elif is_photo:
                tg("editMessageCaption", {
                    "chat_id": message["chat"]["id"],
                    "message_id": message["message_id"],
                    "caption": render(header, answers),
                    "reply_markup": answers_kb(answers),
                })
            else:
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
        body = "Бот працює"
        # Адреса .../api/webhook?debug=1 — перевірка бази (секретів не показує)
        if "debug" in self.path:
            names = sorted(k for k in os.environ if k.startswith(("KV_", "UPSTASH_")))
            lines = [
                f"Змінні бази у Vercel: {', '.join(names) or 'НЕМАЄ'}",
                f"Адреса бази задана: {'так' if REDIS_URL else 'НІ'}",
                f"Ключ бази заданий: {'так' if REDIS_TOKEN else 'НІ'}",
                f"Відповідь бази на PING: {redis('PING')}",
            ]
            for chat in redis("SMEMBERS", "chats") or []:
                lines.append(f"Чат {chat}: запам'ятовано питань — {redis('ZCARD', f'polls:{chat}')}")
            body += "\n\n" + "\n".join(lines)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
