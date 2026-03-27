import telebot
import requests
import threading
import time
import os
import logging
from telebot import types
# --- CẤU HÌNH BOT ---
API_TOKEN = os.getenv("BOT_TOKEN")

# === ADMIN CONFIG ===
ADMIN_ID = os.getenv("ADMIN_ID")

# --- CẤU HÌNH API MUA HÀNG ---
API_TOKEN_SHOP = os.getenv("SHOP_TOKEN")

# KIỂM TRA CÁC BIẾN MÔI TRƯỜNG
# Cấu hình logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
if not API_TOKEN or not ADMIN_ID or not API_TOKEN_SHOP:
    logger.error("❌ Thiếu biến môi trường!")
    exit()

ADMIN_ID = int(ADMIN_ID)

bot = telebot.TeleBot(API_TOKEN, num_threads=10)

DB_FILE = "database_mail.txt"
user_active = {}
scanning_events = {}

# ==================== DECORATOR KIỂM TRA ADMIN ====================
def admin_only(func):
    def wrapper(message):
        if message.chat.id != ADMIN_ID:
            bot.send_message(message.chat.id, "🚫 **BẠN KHÔNG CÓ QUYỀN SỬ DỤNG BOT NÀY!**\nChỉ Admin mới được phép sử dụng.", parse_mode="Markdown")
            return
        return func(message)
    return wrapper

def admin_only_callback(func):
    def wrapper(call):
        if call.message.chat.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "🚫 Bạn không có quyền sử dụng!", show_alert=True)
            return
        return func(call)
    return wrapper

# ==================== HÀM MUA HÀNG (FIX CHUẨN) ====================
def buy_product(product_id, qty=1, coupon=None):
    """Mua hàng với product_id và số lượng - Phiên bản fix chuẩn"""
    url = "https://aviammo.com/api/buy-product.php"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0"
    }

    data = {
        "product_id": product_id,
        "qty": qty
    }

    if coupon:
        data["coupon"] = coupon

    # Log để debug
    try:
        res = requests.post(url, headers=headers, data=data, timeout=15) # Tăng timeout lên 15s

        logger.info(f"📡 API STATUS: {res.status_code}")
        logger.debug(f"📝 API RAW: {res.text[:300]}") # Log raw text, giới hạn 300 ký tự

        # Kiểm tra nếu API trả về rỗng hoặc HTML
        if not res.text.strip():
            return {"success": False, "message": "API trả về rỗng!"}

        if "html" in res.text.lower():
            return {"success": False, "message": "API trả về HTML (có thể bị chặn / sai token)"}

        return res.json()

    except Exception as e: # Bắt tất cả các lỗi liên quan đến request
        return {"success": False, "message": f"Lỗi request API mua hàng: {e}"}

# ==================== HÀM XỬ LÝ MAIL ====================
def get_list_db():
    if not os.path.exists(DB_FILE): return []
    with open(DB_FILE, "r") as f:
        return [l.strip() for l in f.readlines() if "|" in l]

def detect_service_pro(content, chosen_service):
    content = content.lower()
    if any(x in content for x in ["facebook", "fb", "meta"]): return "FACEBOOK 🔵"
    if "tiktok" in content: return "TIKTOK ⚫"
    if any(x in content for x in ["google", "g-", "youtube"]): return "GOOGLE 🔴"
    return chosen_service.upper() + " ✅" if chosen_service != "all" else "HỆ THỐNG 🌐"

def call_api(email, token, client_id, service):
    url = "https://tools.dongvanfb.net/api/get_code_oauth2"
    payload = {"email": email, "refresh_token": token, "client_id": client_id, "type": service}
    try:
        res = requests.post(url, json=payload, timeout=8)
        res.raise_for_status()  # Báo lỗi nếu status code là 4xx hoặc 5xx
        return res.json()
    except (requests.exceptions.RequestException, requests.exceptions.JSONDecodeError) as e:
        # Thay print bằng logging trong môi trường production
        logger.error(f"Lỗi khi gọi API get_code_oauth2: {e}")
        return None

def loop_scan(chat_id, email, token, client_id, service, stop_event, timeout_seconds=300):
    """Quét mã, có mã là dừng ngay"""
    seen_codes = set()
    start_time = time.time()
    
    # Check mã cũ
    data = call_api(email, token, client_id, service)
    if data and data.get("status"):
        code = str(data.get("code"))
        seen_codes.add(code)
        brand = detect_service_pro(data.get("content", ""), service)
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(f"📋 Copy Mã: {code}", callback_data=f"cp:{code}"))
        bot.send_message(chat_id, f"📜 **MÃ HIỆN TẠI:**\n🔹 Dịch vụ: **{brand}**\n🔢 Code: `{code}`", reply_markup=markup, parse_mode="Markdown")
        bot.send_message(chat_id, f"🔄 Đang chờ mã mới... (sẽ dừng ngay khi có mã mới)")
    
    # Vòng lặp quét
    while not stop_event.is_set():
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            bot.send_message(chat_id, f"⏰ Đã hết {timeout_seconds//60} phút, không nhận được mã mới. Dừng quét.")
            break
            
        data = call_api(email, token, client_id, service)
        if data and data.get("status"):
            new_code = str(data.get("code"))
            
            if new_code not in seen_codes:
                brand_new = detect_service_pro(data.get("content", ""), service)
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(f"🔥 Copy MÃ MỚI: {new_code}", callback_data=f"cp:{new_code}"))
                bot.send_message(chat_id, f"🔥 **CÓ MÃ MỚI VỀ!**\n🔹 Dịch vụ: **{brand_new}**\n🔢 **CODE: `{new_code}`**", reply_markup=markup, parse_mode="Markdown")
                bot.send_message(chat_id, f"✅ Đã có mã mới, dừng quét!")
                stop_event.set()
                break
        time.sleep(6)
    
    if chat_id in scanning_events:
        del scanning_events[chat_id]

# ==================== LỆNH BOT ====================
@bot.message_handler(commands=['start'])
@admin_only
def welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("Facebook 🔵", "TikTok ⚫", "Google 🔴")
    markup.row("Tất cả (All) 🌐", "🗂️ Kho Hotmail")
    markup.row("🔍 Check Live All", "🛑 Dừng quét")
    markup.row("💰 Mua 1 SP", "📧 Đọc mail ngay")
    bot.send_message(message.chat.id, 
                     "🚀 **Bot Hotmail Siêu Tốc Active!**\n"
                     f"👑 Admin ID: `{ADMIN_ID}`\n"
                     "💡 Bấm **💰 Mua 1 SP** để mua sản phẩm 894\n"
                     "📦 Mỗi lần mua = 1 account duy nhất",
                     parse_mode="Markdown", 
                     reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "💰 Mua 1 SP")
@admin_only
def buy_one_sp(message):
    """Mua 1 sản phẩm duy nhất (product_id = 894, qty = 1)"""
    chat_id = message.chat.id
    
    bot.send_message(chat_id, "🛒 **Đang mua sản phẩm 894...**\n📦 Mỗi lần chỉ mua **1** account", parse_mode="Markdown")

    result = None
    for _ in range(2): # Thử lại tối đa 2 lần nếu API lỗi
        result = buy_product(894, qty=1)
        if result and result.get("success") == True: # Fix crash khi lỗi JSON
            break
        time.sleep(1) # Đợi 1 giây trước khi thử lại

    # Kiểm tra kết quả
    if result and result.get("success") == True: # Fix crash khi lỗi JSON
        order = result["data"]
        
        # Gửi thông tin đơn hàng
        bot.send_message(chat_id, 
                         f"✅ **MUA THÀNH CÔNG!**\n"
                         f"📦 Mã đơn: `{order['order_code']}`\n"
                         f"💸 Thanh toán: {order['total_pay']}đ",
                         parse_mode="Markdown")
        
        # Lấy và xử lý từng account
        for item in order["items"]:
            acc_line = item["account"]
            bot.send_message(chat_id, 
                             f"📧 **ACC NHẬN ĐƯỢC:**\n`{acc_line}`",
                             parse_mode="Markdown")
            
            # Lưu mail vào kho nếu đúng format
            if "|" in acc_line:
                parts_acc = acc_line.split("|")
                if len(parts_acc) >= 4:
                    email = parts_acc[0].strip()
                    token = parts_acc[2].strip()
                    client_id = parts_acc[3].strip()
                    
                    # Lưu vào database
                    with open(DB_FILE, "a+") as f:
                        f.write(acc_line + "\n")
                    bot.send_message(chat_id, f"💾 Đã lưu mail `{email}` vào kho!", parse_mode="Markdown")
                    
                    # Tự động quét mã
                    bot.send_message(chat_id, 
                                     f"🚀 **Tự động quét mã từ email:** `{email}`\n"
                                     f"⏱️ Sẽ quét trong 5 phút (dừng ngay khi có mã mới)",
                                     parse_mode="Markdown")
                    
                    # Dừng quét cũ nếu có
                    if chat_id in scanning_events:
                        scanning_events[chat_id].set()
                        time.sleep(0.5)
                    
                    stop_event = threading.Event()
                    scanning_events[chat_id] = stop_event
                    threading.Thread(target=loop_scan, args=(chat_id, email, token, client_id, "all", stop_event, 300), daemon=True).start() # Fix thread Railway
                    break  # Chỉ xử lý email đầu tiên
    else:
        error_msg = result.get('message', 'Không rõ lỗi') if result else "Không nhận được phản hồi từ API"
        bot.send_message(chat_id, f"❌ **MUA THẤT BẠI!**\n{error_msg}", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📧 Đọc mail ngay")
@admin_only
def read_mail_now(message):
    chat_id = message.chat.id
    db_mails = get_list_db()
    if not db_mails:
        bot.send_message(chat_id, "📭 Kho mail trống! Hãy mua hàng trước.")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for line in db_mails[:10]:
        email = line.split("|")[0]
        markup.add(types.InlineKeyboardButton(f"📧 {email}", callback_data=f"read:{email}"))
    markup.add(types.InlineKeyboardButton("❌ Đóng", callback_data="close"))
    bot.send_message(chat_id, "📬 **Chọn email để đọc code:**", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🛑 Dừng quét")
@admin_only
def stop_all(message):
    if message.chat.id in scanning_events:
        scanning_events[message.chat.id].set()
        bot.send_message(message.chat.id, "🛑 Đã dừng mọi tiến trình.")
    else:
        bot.send_message(message.chat.id, "ℹ️ Hiện không có tiến trình nào đang chạy.")

@bot.message_handler(func=lambda m: m.text == "🗂️ Kho Hotmail")
@admin_only
def show_mailbox(message):
    db = get_list_db()
    if not db:
        bot.send_message(message.chat.id, "📭 Kho trống!")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for line in db[:20]:
        email = line.split("|")[0]
        markup.add(types.InlineKeyboardButton(email, callback_data=f"info:{email}"))
    bot.send_message(message.chat.id, f"📦 **Kho Hotmail** (Tổng: {len(db)} mail)", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🔍 Check Live All")
@admin_only
def check_all_live(message):
    bot.send_message(message.chat.id, "🔍 Đang kiểm tra... (Tính năng đang phát triển)")

@bot.message_handler(func=lambda m: "|" in m.text)
@admin_only
def ask_action(message):
    user_active[f"temp_{message.chat.id}"] = message.text.strip()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📥 Chỉ Lưu", callback_data="act_save"),
        types.InlineKeyboardButton("🚀 Quét Luôn", callback_data="act_scan")
    )
    bot.reply_to(message, "⚡ **Phát hiện Mail mới!** Bạn muốn làm gì?", reply_markup=markup)

# ==================== CALLBACK ====================
@bot.callback_query_handler(func=lambda call: True)
@admin_only_callback
def handle_callbacks(call):
    chat_id = call.message.chat.id
    
    if call.data.startswith("cp:"):
        code = call.data.split(":")[1]
        bot.answer_callback_query(call.id, f"✅ Đã Copy: {code}")
    
    elif call.data == "act_save":
        raw = user_active.get(f"temp_{chat_id}")
        if raw:
            with open(DB_FILE, "a+") as f: 
                f.write(raw + "\n")
            bot.edit_message_text("✅ Đã cất vào kho!", chat_id, call.message.message_id)
    
    elif call.data == "act_scan":
        raw = user_active.get(f"temp_{chat_id}")
        if raw:
            p = raw.split('|')
            if len(p) >= 4:
                if chat_id in scanning_events: 
                    scanning_events[chat_id].set()
                
                stop_event = threading.Event()
                scanning_events[chat_id] = stop_event
                user_active[chat_id] = {"email": p[0], "token": p[2], "id": p[3]}
                
                bot.edit_message_text(f"🚀 Đang quét: `{p[0]}`", chat_id, call.message.message_id, parse_mode="Markdown")
                threading.Thread(target=loop_scan, args=(chat_id, p[0], p[2], p[3], "all", stop_event, 300), daemon=True).start() # Fix thread Railway
    
    elif call.data.startswith("read:"):
        handle_read_mail_callback(call)
    
    elif call.data.startswith("info:"):
        handle_info_callback(call)
    
    elif call.data == "close":
        bot.delete_message(chat_id, call.message.message_id)

def handle_read_mail_callback(call):
    chat_id = call.message.chat.id
    email_target = call.data.split(":")[1]
    
    for line in get_list_db():
        if line.startswith(email_target):
            parts = line.split("|")
            if len(parts) >= 4:
                email, password, token, client_id = parts[0], parts[1], parts[2], parts[3]
                
                bot.answer_callback_query(call.id, "🔄 Đang lấy code...")
                bot.edit_message_text(f"🔍 Đang kiểm tra mail: `{email}`", chat_id, call.message.message_id, parse_mode="Markdown")
                
                data = call_api(email, token, client_id, "all")
                if data and data.get("status"):
                    code = data.get("code")
                    content = data.get("content", "")
                    brand = detect_service_pro(content, "all")
                    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(f"📋 Copy Code: {code}", callback_data=f"cp:{code}"))
                    bot.send_message(chat_id, f"✅ **CODE TỪ {email}**\n🔹 Dịch vụ: **{brand}**\n🔢 Code: `{code}`", reply_markup=markup, parse_mode="Markdown")
                else:
                    bot.send_message(chat_id, f"❌ Không tìm thấy code cho {email}")
                return
    bot.send_message(chat_id, "❌ Không tìm thấy email trong kho!")

def handle_info_callback(call):
    email_sel = call.data.split(":")[1]
    for line in get_list_db():
        if line.startswith(email_sel):
            p = line.split('|')
            if len(p) >= 4:
                user_active[call.message.chat.id] = {"email": p[0], "token": p[2], "id": p[3]}
                bot.edit_message_text(f"💎 **Đã chọn:** `{p[0]}`\nBấm nút ở Menu để quét!", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                break

# ==================== CHẠY BOT ====================
if __name__ == "__main__":
    logger.info(f"🤖 Bot đang khởi động...")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"🔒 Chỉ admin mới được sử dụng bot")
    logger.info(f"📦 Nút Mua 1 SP: product_id=894, qty=1 (có retry)")
    logger.info(f"🔑 Đã fix hàm buy_product với Bearer token và kiểm tra phản hồi")
    try:
        bot.infinity_polling(skip_pending=True) # Fix lỗi Railway (treo bot / crash ngầm)
    except telebot.apihelper.ApiTelegramException as e:
        logger.critical(f"❌ Lỗi nghiêm trọng khi khởi động bot: {e}. Đảm bảo chỉ có MỘT instance bot đang chạy!")
        exit(1)