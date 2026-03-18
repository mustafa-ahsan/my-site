import nest_asyncio
nest_asyncio.apply()

import requests
import logging
import os
import yt_dlp
import threading
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- 1. إعداد السجلات (Logs) ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log", encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- الإعدادات الأساسية ---
# من الأفضل جلب التوكن من متغيرات البيئة في Render، وإذا لم يجده سيستخدم هذا التوكن
BOT_TOKEN = os.environ.get("BOT_TOKEN", "6331034512:AAHf-B56fuIRRMXsDblHgFMPPTJJ2TTXg4E")
ADMIN_ID = 750512813
ADSGRAM_BASE_URL = "https://your-adsgram-link.com/watch" # ⚠️ ضع رابط إعلان AdsGram هنا

user_downloads = {}
pending_downloads = {} # لحفظ رابط الفيديو مؤقتاً أثناء مشاهدة الإعلان
USERS_FILE = "users.txt"

# --- 2. إعداد خادم Flask (لـ Render واستقبال رد AdsGram) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "سيرفر البوت يعمل بنجاح!"

@app.route('/adsgram_callback', methods=['GET', 'POST'])
def adsgram_callback():
    user_id = request.args.get('userid') or request.form.get('userid')
    
    if user_id:
        user_id = int(user_id)
        if user_id in pending_downloads:
            video_url = pending_downloads[user_id]
            
            # إرسال رسالة نجاح عبر Telegram API المباشر (لتجنب مشاكل Async Threading)
            api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            requests.post(api_url, json={"chat_id": user_id, "text": "✅ شكراً لك! تم التحقق من مشاهدة الإعلان. جاري إرسال الفيديو..."})
            
            # إرسال الفيديو
            video_api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
            response = requests.post(video_api_url, json={"chat_id": user_id, "video": video_url})
            
            if response.status_code == 200:
                user_downloads[user_id] = user_downloads.get(user_id, 0) + 1
            else:
                requests.post(api_url, json={"chat_id": user_id, "text": "❌ عذراً، حجم الفيديو كبير جداً على خوادم تيليجرام."})
            
            # مسح الطلب المؤقت
            del pending_downloads[user_id]
            return "Success", 200
            
    return "Failed", 400

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- دوال التعامل مع ملف المستخدمين ---
def save_user(user_id):
    users = get_all_users()
    if str(user_id) not in users:
        with open(USERS_FILE, "a") as f:
            f.write(f"{user_id}\n")

def get_all_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r") as f:
        return f.read().splitlines()

# --- وظائف سحب الفيديوهات ---
def get_tiktok_video(video_url):
    api_url = "https://tikwm.com/api/"
    try:
        response = requests.get(api_url, params={"url": video_url, "hd": 1}, timeout=10)
        data = response.json()
        if data.get("code") == 0:
            return data["data"]["play"]
    except Exception as e:
        logger.error(f"TikTok API Error: {e}")
    return None

def get_instagram_video(video_url):
    clean_url = video_url.split('?')[0]
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'cookiefile': 'cookies.txt' 
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            return info.get('url')
    except Exception as e:
        logger.error(f"yt-dlp Error: {e}")
    return None

# --- معالجة الأوامر والرسائل ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    save_user(user_id)
    await update.message.reply_text("أهلاً بك! 🎥\nأرسل رابط تيك توك أو انستكرام (Reels) للتحميل بدون علامة مائية 🚀")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    text = update.message.text
    save_user(user_id)

    if "tiktok.com" in text:
        platform = "tiktok"
    elif "instagram.com" in text:
        platform = "instagram"
    else:
        await update.message.reply_text("الرجاء إرسال رابط تيك توك أو انستكرام (Reels) صحيح ⚠️")
        return

    wait_msg = await update.message.reply_text("⏳ جاري سحب الفيديو، يرجى الانتظار...")

    if platform == "tiktok":
        video_url = get_tiktok_video(text)
    else:
        video_url = get_instagram_video(text)

    if not video_url:
        await wait_msg.edit_text("❌ فشل العثور على الفيديو. تأكد أن الحساب عام (Public).")
        return

    if user_id not in user_downloads:
        user_downloads[user_id] = 0

    downloads = user_downloads[user_id]

    # أول محاولتين مجانية
    if downloads < 2:
        try:
            await update.message.reply_video(video=video_url, caption="✅ تم التحميل بنجاح!")
            user_downloads[user_id] += 1
            await wait_msg.delete()
        except Exception as e:
            await wait_msg.edit_text("❌ الفيديو حجمه كبير جداً أو حدث خطأ أثناء الإرسال.")
            logger.error(f"Telegram Send Error: {e}")
    else:
        # نظام AdsGram الجديد
        pending_downloads[user_id] = video_url
        ad_url = f"{ADSGRAM_BASE_URL}?userid={user_id}"
        
        keyboard = [[InlineKeyboardButton("مشاهدة الإعلان للتحميل 📥", url=ad_url)]]
        await wait_msg.edit_text(
            "تجاوزت الحد المجاني 🚀\nلتحميل هذا الفيديو، يرجى مشاهدة إعلان قصير جداً لدعم البوت. (سيصلك الفيديو تلقائياً بعد الإعلان):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# --- نظام الإذاعة ---
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != ADMIN_ID:
        return

    message_to_send = update.message.text.replace("/broadcast", "").strip()
    if not message_to_send:
        await update.message.reply_text("الرجاء كتابة الرسالة بعد الأمر.")
        return

    users = get_all_users()
    if not users:
        await update.message.reply_text("لا يوجد مستخدمين.")
        return

    await update.message.reply_text(f"⏳ جاري الإرسال إلى {len(users)} مستخدم...")
    success_count = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=message_to_send)
            success_count += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ تمت الإذاعة بنجاح! وصلت إلى: {success_count} مستخدم.")

# --- تشغيل البوت الأساسي ---
def main():
    # 1. تشغيل خادم Flask في الخلفية ليوافق متطلبات Render
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. تشغيل بوت التيليجرام
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 البوت وخادم الويب يعملان الآن...")
    app.run_polling()

if __name__ == '__main__':
    main()
