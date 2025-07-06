import os
from datetime import datetime
from collections import deque

from flask import Flask, request, abort, jsonify
from flask_sqlalchemy import SQLAlchemy
import requests
import openai
from dotenv import load_dotenv

# ░█ 1) تحميل متغيّرات البيئة
load_dotenv()

OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN      = os.getenv("VERIFY_TOKEN")
ADMIN_PASSWORD    = os.getenv("ADMIN_PASSWORD")

# تحقّق مبكّر: إذا نقص متغيّر بيئي اخرج برسالة واضحة
required_vars = {
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "PAGE_ACCESS_TOKEN": PAGE_ACCESS_TOKEN,
    "VERIFY_TOKEN": VERIFY_TOKEN,
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
}
missing = [k for k, v in required_vars.items() if not v]
if missing:
    raise RuntimeError(f"❌ متغيرات البيئة التالية مفقودة: {', '.join(missing)}")

openai.api_key = OPENAI_API_KEY

# ░█ 2) إعداد Flask و SQLAlchemy
app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite:///users.db",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
db = SQLAlchemy(app)

# ░█ 3) نموذج المستخدم
class User(db.Model):
    id = db.Column(db.String, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    messages = db.Column(db.Integer, default=0)

with app.app_context():
    db.create_all()
    print("✅ قاعدة البيانات جاهزة.")

# ░█ 4) إدارة السياق في الذاكرة
CONTEXT_LIMIT = 10
user_contexts: dict[str, deque] = {}

def update_context(uid: str, role: str, content: str) -> None:
    user_contexts.setdefault(uid, deque(maxlen=CONTEXT_LIMIT)).append(
        {"role": role, "content": content}
    )

def get_context(uid: str) -> list[dict]:
    return list(user_contexts.get(uid, []))

# ░█ 5) تخزين عدّاد المحادثات
def log_conversation(uid: str) -> None:
    user = db.session.get(User, uid) or User(id=uid)
    user.messages = (user.messages or 0) + 1
    db.session.add(user)
    db.session.commit()

# ░█ 6) إرسال رسالة إلى Messenger
def send_message(recipient_id: str, text: str, quick_replies: list | None = None) -> None:
    url = "https://graph.facebook.com/v18.0/me/messages"
    payload = {
        "messaging_type": "RESPONSE",
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    if quick_replies:
        payload["message"]["quick_replies"] = quick_replies
    try:
        requests.post(url, params={"access_token": PAGE_ACCESS_TOKEN}, json=payload, timeout=10).raise_for_status()
    except requests.RequestException as e:
        print(f"❌ خطأ في إرسال رسالة: {e}")

# ░█ 7) سؤال GPT
def ask_gpt(uid: str, user_msg: str) -> str:
    update_context(uid, "user", user_msg)
    full_messages = [{"role": "system", "content": "أجب بإيجاز وبأسلوب ودود."}] + get_context(uid)
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=full_messages,
            temperature=0.7,
        )
        bot_reply = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ خطأ من OpenAI: {e}")
        bot_reply = "عذرًا، تعذّر الردّ حاليًا."
    update_context(uid, "assistant", bot_reply)
    return bot_reply

# ░█ 8) معالجة الرسائل
def process_message(uid: str, text: str):
    if any(k in text.lower() for k in ["دعم بشري", "موظف", "مساعدة حقيقية", "تحدث إلى شخص"]):
        send_message(uid, "تم تحويلك للدعم البشري، يرجى الانتظار.")
        return
    reply = ask_gpt(uid, text)
    log_conversation(uid)
    qr = [
        {"content_type": "text", "title": "دعم بشري", "payload": "HUMAN_SUPPORT"},
        {"content_type": "text", "title": "معلومات عن الخدمات", "payload": "SERVICE_INFO"},
    ]
    send_message(uid, reply, quick_replies=qr)

# ░█ 9) Webhook GET (التحقق)
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge", "")
    return "Invalid token", 403

# ░█ 10) Webhook POST (استقبال الرسائل)
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for ev in entry.get("messaging", []):
            uid = ev.get("sender", {}).get("id")
            msg = ev.get("message", {})
            if uid and "text" in msg:
                if msg.get("quick_reply"):
                    payload = msg["quick_reply"]["payload"]
                    if payload == "HUMAN_SUPPORT":
                        send_message(uid, "تم تحويلك للدعم البشري، يرجى الانتظار.")
                    elif payload == "SERVICE_INFO":
                        send_message(uid, "نقدم خدماتنا على مدار الساعة. كيف أساعدك؟")
                    continue
                process_message(uid, msg["text"])
    return jsonify(status="ok"), 200

# ░█ 11) صفحة إحصاءات بسيطة
@app.route("/stats")
def stats():
    if request.args.get("pwd") != ADMIN_PASSWORD:
        return abort(403)
    users = User.query.count()
    msgs = db.session.query(db.func.sum(User.messages)).scalar() or 0
    return f"<h1>👤 المستخدمون: {users}</h1><h2>📨 الرسائل: {msgs}</h2>"

# ░█ 12) تشغيل السيرفر
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
