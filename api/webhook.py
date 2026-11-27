# Бот «Как обстановка?» для Vercel (без постоянно запущенного сервера)
# Положите этот файл в репозиторий GitHub по пути: api/webhook.py
# В настройках проекта на Vercel добавьте переменную окружения BOT_TOKEN.
# Никаких дополнительных библиотек не нужно.

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

TOKEN = os.environ.get("BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}/"

QUESTION = "Как обстановка?"
OPTIONS = ["Тихо", "Летает Физтех", "Летает БАМ", "Летает Бульвар"]

# Постоянная кнопка внизу экрана
MAIN_KB = {
    "keyboard": [[{"text": QUESTION}]],
    "resize_keyboard": True,
    "is_persistent": True,
}

# Кнопки ответа под вопросом
ANSWERS_KB = {
    "inline_keyboard": [
        [{"text": opt, "callback_data": f"v:{i}"}] for i, opt in enumerate(OPTIONS)
    ]
}


def tg(method, data):
    req = urllib.request.Request(
        API + method,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("Telegram error:", method, e)


def now_kyiv():
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Kyiv")
    except Exception:
        tz = timezone(timedelta(hours=2))
    return datetime.now(tz).strftime("%H:%M")


def full_name(user):
    name = " ".join(p for p in [user.get("first_name"), user.get("last_name")] if p)
    return (name or "Без имени").replace(",", " ").replace("\n", " ").strip()


def render(header, answers):
    """answers: {имя: номер_ответа}"""
    blocks = [header]
    for i, opt in enumerate(OPTIONS):
        names = [n for n, idx in answers.items() if idx == i]
        block = f"{opt} — {len(names)}"
        if names:
            block += "\n" + ", ".join(names)
        blocks.append(block)
    return "\n\n".join(blocks)


def parse(text):
    """Восстанавливает ответы из текста сообщения — так боту не нужна база данных."""
    blocks = text.split("\n\n")
    header = blocks[0]
    answers = {}
    for block in blocks[1:]:
        lines = block.split("\n")
        title = lines[0].rsplit(" — ", 1)[0]
        if title in OPTIONS and len(lines) > 1:
            idx = OPTIONS.index(title)
            for name in lines[1].split(", "):
                if name:
                    answers[name] = idx
    return header, answers


def process(update):
    msg = update.get("message")
    if msg and "text" in msg:
        text = msg["text"].strip()
        chat_id = msg["chat"]["id"]
        if text.startswith("/start"):
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "Нажмите «Как обстановка?», чтобы спросить всех.",
                "reply_markup": MAIN_KB,
            })
        elif text == QUESTION:
            header = f"❓ {QUESTION}\n{now_kyiv()}, спрашивает {full_name(msg['from'])}"
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": render(header, {}),
                "reply_markup": ANSWERS_KB,
            })
        return

    call = update.get("callback_query")
    if call and call.get("data", "").startswith("v:") and "message" in call:
        idx = int(call["data"].split(":")[1])
        message = call["message"]
        header, answers = parse(message.get("text", ""))
        name = full_name(call["from"])

        # Один человек — один ответ. Повторное нажатие меняет ответ.
        if answers.get(name) != idx:
            answers.pop(name, None)
            answers[name] = idx
            tg("editMessageText", {
                "chat_id": message["chat"]["id"],
                "message_id": message["message_id"],
                "text": render(header, answers),
                "reply_markup": ANSWERS_KB,
            })
        tg("answerCallbackQuery", {
            "callback_query_id": call["id"],
            "text": f"Вы ответили: {OPTIONS[idx]}",
        })


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
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Бот работает".encode("utf-8"))

