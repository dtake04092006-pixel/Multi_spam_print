import discord, asyncio, threading, time, os, re, requests, json, random, traceback, uuid
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
import numpy as np
import pytesseract
from PIL import Image, ImageOps, ImageEnhance
import io

# --- CẤU HÌNH OCR ---
pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'

load_dotenv()

# --- CẤU HÌNH ---
main_tokens = os.getenv("MAIN_TOKENS", "").split(",")
tokens = os.getenv("TOKENS", "").split(",")
karuta_id, karibbit_id = "646937666251915264", "1311684840462225440"
BOT_NAMES = ["xsyx", "sofa", "dont", "ayaya", "owo", "astra", "singo", "dia pox", "clam", "rambo", "domixi", "dogi", "sicula", "mo turn", "jan taru", "kio sama"]
acc_names = [f"Bot-{i:02d}" for i in range(1, 21)]

# --- BIẾN TRẠNG THÁI & KHÓA ---
servers = []
bot_states = {
    "reboot_settings": {}, "active": {}, "watermelon_grab": {}, "health_stats": {},
}
server_start_time = time.time()

# --- BIẾN CHIA SẺ THÔNG TIN GIỮA CÁC BOT ---
shared_drop_info = {
    "heart_data": None,
    "ocr_data": None,
    "message_id": None,
    "timestamp": 0,
    "lock": threading.Lock()
}

# --- QUẢN LÝ BOT THREAD-SAFE ---
class ThreadSafeBotManager:
    def __init__(self):
        self._bots = {}
        self._rebooting = set()
        self._lock = threading.RLock()

    def add_bot(self, bot_id, bot_data):
        with self._lock: 
            self._bots[bot_id] = bot_data

    def remove_bot(self, bot_id):
        with self._lock:
            bot_data = self._bots.pop(bot_id, None)
            if bot_data and bot_data.get('instance'):
                try:
                    bot_instance = bot_data['instance']
                    bot_loop = bot_data.get('loop')
                    if bot_loop and not bot_loop.is_closed():
                        asyncio.run_coroutine_threadsafe(bot_instance.close(), bot_loop)
                except Exception as e:
                    print(f"[BotManager] ⚠️ Lỗi khi đóng bot {bot_id}: {e}", flush=True)
            return bot_data

    def get_bot_data(self, bot_id):
        with self._lock: return self._bots.get(bot_id)

    def get_all_bots_data(self):
        with self._lock: return list(self._bots.items())
    
    def get_main_bots_info(self):
        with self._lock: return [(bot_id, data) for bot_id, data in self._bots.items() if bot_id.startswith('main_')]

    def get_sub_bots_info(self):
        with self._lock: return [(bot_id, data) for bot_id, data in self._bots.items() if bot_id.startswith('sub_')]

    def is_rebooting(self, bot_id):
        with self._lock: return bot_id in self._rebooting

    def start_reboot(self, bot_id):
        with self._lock:
            if self.is_rebooting(bot_id): return False
            self._rebooting.add(bot_id)
            return True

    def end_reboot(self, bot_id):
        with self._lock: self._rebooting.discard(bot_id)

bot_manager = ThreadSafeBotManager()

# --- LƯU & TẢI CÀI ĐẶT ---
def save_settings():
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    settings_data = {'servers': servers, 'bot_states': bot_states, 'last_save_time': time.time()}
    if api_key and bin_id:
        headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
        url = f"https://api.jsonbin.io/v3/b/{bin_id}"
        try: requests.put(url, json=settings_data, headers=headers, timeout=15)
        except: pass
    try:
        with open('backup_settings.json', 'w') as f: json.dump(settings_data, f, indent=2)
    except: pass

def load_settings():
    global servers, bot_states
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    if api_key and bin_id:
        try:
            headers = {'X-Master-Key': api_key}
            url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
            req = requests.get(url, headers=headers, timeout=15)
            if req.status_code == 200:
                data = req.json().get("record", {})
                servers.extend(data.get('servers', []))
                bot_states.update(data.get('bot_states', {}))
                return
        except: pass
    try:
        with open('backup_settings.json', 'r') as f:
            data = json.load(f)
            servers.extend(data.get('servers', []))
            bot_states.update(data.get('bot_states', {}))
    except: pass

def get_bot_name(bot_id_str):
    try:
        parts = bot_id_str.split('_')
        if parts[0] == 'main': return BOT_NAMES[int(parts[1]) - 1]
        return acc_names[int(parts[1])]
    except: return bot_id_str

# --- CÁC HÀM HỖ TRỢ ---
def periodic_task(interval, task_func, task_name):
    print(f"[{task_name}] 🚀 Khởi động luồng định kỳ.", flush=True)
    while True:
        time.sleep(interval)
        try: task_func()
        except Exception as e: print(f"[{task_name}] ❌ Lỗi: {e}", flush=True)

def check_bot_health(bot_data, bot_id):
    try:
        stats = bot_states["health_stats"].setdefault(bot_id, {'consecutive_failures': 0, 'last_check': 0})
        stats['last_check'] = time.time()
        
        if not bot_data or not bot_data.get('instance'):
            stats['consecutive_failures'] += 1
            return False

        bot = bot_data['instance']
        is_connected = bot.is_ready() and not bot.is_closed()
        
        if is_connected:
            stats['consecutive_failures'] = 0
        else:
            stats['consecutive_failures'] += 1
            print(f"[Health Check] ⚠️ Bot {bot_id} not connected - failures: {stats['consecutive_failures']}", flush=True)
            
        return is_connected
    except Exception as e:
        print(f"[Health Check] ❌ Exception in health check for {bot_id}: {e}", flush=True)
        return False

def health_monitoring_check():
    all_bots = bot_manager.get_all_bots_data()
    for bot_id, bot_data in all_bots:
        check_bot_health(bot_data, bot_id)

# ==============================================================================
# <<< XỬ LÝ ẢNH (OCR) - PHIÊN BẢN PIL >>>
# ==============================================================================
def scan_image_for_prints(image_url):
    print(f"[OCR LOG] 📥 Đang tải ảnh từ URL...", flush=True)
    try:
        resp = requests.get(image_url, timeout=5)
        if resp.status_code != 200: return []
        
        img = Image.open(io.BytesIO(resp.content))
        width, height = img.size
        
        num_cards = 3 
        if width > 1000: num_cards = 4
        
        card_width = width // num_cards
        results = []

        print(f"[OCR LOG] 🖼️ Ảnh size {width}x{height}. Chia làm {num_cards} cột (PIL Mode).", flush=True)

        for i in range(num_cards):
            left = i * card_width
            right = (i + 1) * card_width
            
            print_crop_top = int(height * 0.86) 
            
            crop_img = img.crop((left, print_crop_top, right, height))

            crop_img = crop_img.convert('L') 
            
            enhancer = ImageEnhance.Contrast(crop_img)
            crop_img = enhancer.enhance(2.0) 
            
            crop_img = ImageOps.invert(crop_img)

            custom_config = r'--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789'
            
            text = pytesseract.image_to_string(crop_img, config=custom_config)
            
            numbers = re.findall(r'\d+', text)
            
            if numbers:
                int_numbers = [int(n) for n in numbers]
                print_num = max(int_numbers)
                
                results.append((i, print_num))
                print(f"[OCR LOG] 👁️ Thẻ {i+1}: Đọc được Print = {print_num} (Raw: '{text.strip()}')", flush=True)
            else:
                 print(f"[OCR LOG] 👁️ Thẻ {i+1}: Không đọc được số. (Raw: '{text.strip()}')", flush=True)

        return results

    except Exception as e:
        print(f"[OCR LOG] ❌ Lỗi xử lý ảnh: {e}", flush=True)
        traceback.print_exc()
        return []

# ==============================================================================
# <<< LOGIC NHẶT THẺ - PHIÊN BẢN MỚI (CHỈ BOT 1 ĐỌC, CHIA SẺ CHO CÁC BOT KHÁC) >>>
# ==============================================================================
async def scan_and_share_drop_info(bot, msg, channel_id):
    """Bot 1 quét thông tin và chia sẻ cho tất cả bot khác"""
    
    with shared_drop_info["lock"]:
        # Reset data
        shared_drop_info["heart_data"] = None
        shared_drop_info["ocr_data"] = None
        shared_drop_info["message_id"] = msg.id
        shared_drop_info["timestamp"] = time.time()
    
    print(f"[SCAN] 🔍 Bot 1 đang quét thông tin drop...", flush=True)
    
    # Tải lại tin nhắn
    try:
        msg = await msg.channel.fetch_message(msg.id)
    except Exception as e:
        print(f"[SCAN] ⚠️ Lỗi fetch message: {e}", flush=True)
        return
    
    # 1. QUÉT TIM (NHANH NHẤT - ƯU TIÊN)
    heart_data = None
    try:
        async for msg_item in msg.channel.history(limit=3):
            if msg_item.author.id == int(karibbit_id) and msg_item.created_at > msg.created_at:
                if not msg_item.embeds: continue
                desc = msg_item.embeds[0].description
                if not desc or '♡' not in desc: continue

                lines = desc.split('\n')[:4]
                heart_numbers = [int(re.search(r'♡(\d+)', line).group(1)) if re.search(r'♡(\d+)', line) else 0 for line in lines]
                heart_data = heart_numbers
                print(f"[SCAN] ❤️ Đọc được tim: {heart_data}", flush=True)
                break
    except Exception as e:
        print(f"[SCAN] ⚠️ Lỗi đọc tim: {e}", flush=True)
    
    # 2. QUÉT PRINT (CHẬM HƠN)
    ocr_data = None
    image_url = None
    if msg.embeds and msg.embeds[0].image:
        image_url = msg.embeds[0].image.url
    elif msg.attachments:
        image_url = msg.attachments[0].url
    
    if image_url:
        print(f"[SCAN] 📷 Đang quét ảnh OCR...", flush=True)
        loop = asyncio.get_event_loop()
        ocr_data = await loop.run_in_executor(None, scan_image_for_prints, image_url)
        print(f"[SCAN] 👁️ Kết quả OCR: {ocr_data}", flush=True)
    
    # Lưu vào shared memory
    with shared_drop_info["lock"]:
        shared_drop_info["heart_data"] = heart_data
        shared_drop_info["ocr_data"] = ocr_data
    
    print(f"[SCAN] ✅ Bot 1 hoàn tất quét. Dữ liệu đã được chia sẻ.", flush=True)

async def handle_grab(bot, msg, bot_num):
    """Logic nhặt thẻ cho mỗi bot dựa trên dữ liệu chia sẻ"""
    
    channel_id = msg.channel.id
    target_server = next((s for s in servers if s.get('main_channel_id') == str(channel_id)), None)
    if not target_server: return

    bot_id_str = f'main_{bot_num}'
    auto_grab = target_server.get(f'auto_grab_enabled_{bot_num}', False)
    
    if not auto_grab: 
        return

    # CHỈ BOT 1 QUÉT - CÁC BOT KHÁC CHỜ
    if bot_num == 1:
        await scan_and_share_drop_info(bot, msg, channel_id)
        await asyncio.sleep(0.3)  # Delay nhỏ để bot 1 kịp nhặt trước
    else:
        # Các bot khác chờ 0.5s để bot 1 quét xong
        await asyncio.sleep(random.uniform(0.5, 0.8))
    
    # Lấy dữ liệu chia sẻ
    with shared_drop_info["lock"]:
        if shared_drop_info["message_id"] != msg.id:
            print(f"[GRAB | Bot {bot_num}] ⚠️ Message ID không khớp, bỏ qua.", flush=True)
            return
        
        heart_data = shared_drop_info["heart_data"]
        ocr_data = shared_drop_info["ocr_data"]
    
    # Lấy cấu hình
    grab_mode = target_server.get(f'grab_mode_{bot_num}', 1)  # 1: Tim, 2: Print, 3: Cả hai
    
    heart_min = target_server.get(f'heart_min_{bot_num}', 50)
    heart_max = target_server.get(f'heart_max_{bot_num}', 99999)
    
    print_min = target_server.get(f'print_min_{bot_num}', 1)
    print_max = target_server.get(f'print_max_{bot_num}', 1000)
    
    final_choice = None
    
    # MODE 1: CHỈ TIM (NHANH NHẤT)
    if grab_mode == 1 and heart_data:
        valid_cards = [(idx, hearts) for idx, hearts in enumerate(heart_data) if heart_min <= hearts <= heart_max]
        
        if valid_cards:
            best_idx, best_hearts = max(valid_cards, key=lambda x: x[1])
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            final_choice = (emoji, 0.3, f"Mode 1 - Hearts {best_hearts}")
    
    # MODE 3: CẢ TIM VÀ PRINT (ƯU TIÊN THỨ 2)
    elif grab_mode == 3 and heart_data and ocr_data:
        # Tìm thẻ thỏa MÃN CẢ HAI ĐIỀU KIỆN
        valid_cards = []
        
        # Tạo dict print theo index
        print_dict = {idx: val for idx, val in ocr_data}
        
        for idx, hearts in enumerate(heart_data):
            if idx in print_dict:
                print_val = print_dict[idx]
                # Kiểm tra CẢ tim VÀ print
                if (heart_min <= hearts <= heart_max) and (print_min <= print_val <= print_max):
                    valid_cards.append((idx, hearts, print_val))
        
        if valid_cards:
            # Ưu tiên: Print thấp nhất -> Tim cao nhất
            best = min(valid_cards, key=lambda x: (x[2], -x[1]))
            best_idx, best_hearts, best_print = best
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            final_choice = (emoji, 0.5, f"Mode 3 - Hearts {best_hearts} + Print #{best_print}")
    
    # MODE 2: CHỈ PRINT (CHẬM NHẤT)
    elif grab_mode == 2 and ocr_data:
        valid_prints = [(idx, val) for idx, val in ocr_data if print_min <= val <= print_max]
        
        if valid_prints:
            best_idx, best_print = min(valid_prints, key=lambda x: x[1])
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            final_choice = (emoji, 0.7, f"Mode 2 - Print #{best_print}")
    
    # THỰC HIỆN GRAB
    if final_choice:
        emoji, delay, reason = final_choice
        print(f"[GRAB | Bot {bot_num}] 🎯 Quyết định nhặt {emoji}. Lý do: {reason}", flush=True)
        
        async def grab_action():
            await asyncio.sleep(delay)
            try:
                await msg.add_reaction(emoji)
                ktb_id = target_server.get('ktb_channel_id')
                if ktb_id:
                    ktb = bot.get_channel(int(ktb_id))
                    if ktb: await ktb.send("kt fs")
            except Exception as e:
                print(f"[GRAB] Lỗi react: {e}", flush=True)
        
        asyncio.create_task(grab_action())

# --- KHỞI TẠO BOT ---
def initialize_and_run_bot(token, bot_id_str, is_main, ready_event=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    bot = discord.Client(self_bot=True, heartbeat_timeout=60.0, guild_subscriptions=False)
    
    try: bot_identifier = int(bot_id_str.split('_')[1])
    except: bot_identifier = 99

    @bot.event
    async def on_ready():
        print(f"[Bot] ✅ Login: {bot.user.name} ({bot_id_str})", flush=True)
        if ready_event: ready_event.set()

    @bot.event
    async def on_message(msg):
        if not is_main: return
        
        target_server = next((s for s in servers if s.get('main_channel_id') == str(msg.channel.id)), None)
        
        if not target_server: return

        if "dropping" in msg.content.lower():
            print(f"[DEBUG] 👀 Bot {bot_id_str} thấy Drop tại kênh {msg.channel.id}", flush=True)

        try:
            if (msg.author.id == int(karuta_id) or msg.author.id == int(karibbit_id)) and "dropping" in msg.content.lower():
                print(f"[DEBUG] ✅ PHÁT HIỆN DROP! Đang xử lý...", flush=True)
                await handle_grab(bot, msg, bot_identifier)
        except Exception as e:
            print(f"[Err] {e}", flush=True)

    try:
        bot_manager.add_bot(bot_id_str, {'instance': bot, 'loop': loop})
        loop.run_until_complete(bot.start(token))
    except KeyboardInterrupt: pass
    except Exception as e:
        print(f"[Bot] ❌ Crash {bot_id_str}: {e}", flush=True)
    finally:
        try:
            bot_manager.remove_bot(bot_id_str)
            if not bot.is_closed(): loop.run_until_complete(bot.close())
            if loop.is_running(): loop.stop()
            if not loop.is_closed(): loop.close()
        except: pass

# --- WEB SERVER (UI) ---
app = Flask(__name__)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎴 Shadow OCR Premium Control</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            background: linear-gradient(135deg, #0a0a0a 0%, #1a0a1a 50%, #0a0a1a 100%);
            color: #f0f0f0;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            padding: 20px;
            min-height: 100vh;
        }
        
        .header {
            text-align: center;
            padding: 30px 0;
            background: linear-gradient(135deg, #8b0000, #4b0082);
            border-radius: 15px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(139, 0, 0, 0.5);
        }
        
        .header h1 {
            font-size: 2.5em;
            text-shadow: 0 0 20px rgba(255, 215, 0, 0.8);
            margin-bottom: 10px;
        }
        
        .uptime {
            color: #ffd700;
            font-size: 1.1em;
            text-shadow: 0 0 10px rgba(255, 215, 0, 0.5);
        }
        
        .control-bar {
            display: flex;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .btn {
            background: linear-gradient(135deg, #333, #555);
            color: white;
            border: none;
            padding: 12px 25px;
            cursor: pointer;
            border-radius: 8px;
            font-size: 1em;
            transition: all 0.3s;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #006400, #008000);
        }
        
        .btn-danger {
            background: linear-gradient(135deg, #8b0000, #b22222);
        }
        
        .master-panel {
            background: linear-gradient(135deg, #1a1a2e, #16213e);
            border: 2px solid #ffd700;
            padding: 25px;
            margin-bottom: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 40px rgba(255, 215, 0, 0.3);
        }
        
        .master-panel h2 {
            color: #ffd700;
            text-align: center;
            margin-bottom: 20px;
            font-size: 1.8em;
            text-shadow: 0 0 15px rgba(255, 215, 0, 0.8);
        }
        
        .master-controls {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
        }
        
        .control-group {
            background: rgba(255, 255, 255, 0.05);
            padding: 15px;
            border-radius: 10px;
            border: 1px solid rgba(255, 215, 0, 0.3);
        }
        
        .control-group h4 {
            color: #ffd700;
            margin-bottom: 10px;
            font-size: 1.1em;
        }
        
        .panel {
            background: linear-gradient(135deg, #111, #1a1a1a);
            border: 1px solid #444;
            padding: 25px;
            margin-bottom: 25px;
            border-radius: 15px;
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5);
            transition: all 0.3s;
        }
        
        .panel:hover {
            border-color: #8b0000;
            box-shadow: 0 10px 40px rgba(139, 0, 0, 0.4);
        }
        
        .panel h2 {
            border-bottom: 3px solid #8b0000;
            padding-bottom: 15px;
            color: #ffd700;
            font-size: 1.6em;
            text-shadow: 0 0 10px rgba(255, 215, 0, 0.5);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .input-group {
            margin-bottom: 15px;
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }
        
        .input-group label {
            min-width: 100px;
            color: #ccc;
            font-weight: bold;
        }
        
        input, select {
            background: rgba(0, 0, 0, 0.6);
            border: 1px solid #555;
            color: white;
            padding: 10px;
            border-radius: 6px;
            flex: 1;
            min-width: 100px;
            transition: all 0.3s;
        }
        
        input:focus, select:focus {
            border-color: #ffd700;
            outline: none;
            box-shadow: 0 0 10px rgba(255, 215, 0, 0.3);
        }
        
        .bot-card {
            background: linear-gradient(135deg, #1a1a1a, #2a2a2a);
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 12px;
            border: 1px solid #444;
            transition: all 0.3s;
        }
        
        .bot-card:hover {
            border-color: #ffd700;
            transform: translateX(5px);
        }
        
        .bot-card h3 {
            color: #ffd700;
            margin-bottom: 15px;
            font-size: 1.3em;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .mode-selector {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }
        
        .mode-btn {
            flex: 1;
            padding: 10px;
            background: #333;
            border: 2px solid #555;
            color: white;
            cursor: pointer;
            border-radius: 8px;
            transition: all 0.3s;
            text-align: center;
        }
        
        .mode-btn.active {
            background: linear-gradient(135deg, #ffd700, #ffed4e);
            color: #000;
            border-color: #ffd700;
            box-shadow: 0 0 20px rgba(255, 215, 0, 0.6);
        }
        
        .range-input {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .range-input input {
            flex: 1;
        }
        
        .range-input span {
            color: #ffd700;
            font-weight: bold;
        }
        
        .toggle-btn {
            padding: 10px 20px;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            transition: all 0.3s;
            font-weight: bold;
        }
        
        .toggle-btn.active {
            background: linear-gradient(135deg, #006400, #008000);
            box-shadow: 0 0 20px rgba(0, 255, 0, 0.4);
        }
        
        .delete-server {
            background: linear-gradient(135deg, #8b0000, #b22222);
            padding: 8px 15px;
            border-radius: 6px;
            border: none;
            color: white;
            cursor: pointer;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        
        .pulse {
            animation: pulse 2s infinite;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1><i class="fas fa-crown"></i> Shadow OCR Premium Control <i class="fas fa-crown"></i></h1>
        <div class="uptime pulse">⏱️ Uptime: <span id="uptime">00:00:00</span></div>
    </div>

    <div class="control-bar">
        <button id="add-server-btn" class="btn btn-primary">
            <i class="fas fa-plus-circle"></i> Add New Server
        </button>
        <button id="sync-all-btn" class="btn" style="background: linear-gradient(135deg, #4b0082, #8b008b);">
            <i class="fas fa-sync"></i> Sync All from Master
        </button>
    </div>

    <!-- MASTER CONTROL PANEL -->
    <div class="master-panel">
        <h2><i class="fas fa-cog"></i> Master Control Panel</h2>
        <div class="master-controls">
            <div class="control-group">
                <h4><i class="fas fa-gamepad"></i> Grab Mode</h4>
                <select id="master-mode">
                    <option value="1">Mode 1: Hearts Only</option>
                    <option value="2">Mode 2: Print Only</option>
                    <option value="3">Mode 3: Hearts + Print</option>
                </select>
            </div>
            
            <div class="control-group">
                <h4><i class="fas fa-heart"></i> Hearts Range</h4>
                <div class="range-input">
                    <input type="number" id="master-heart-min" placeholder="Min" value="50">
                    <span>~</span>
                    <input type="number" id="master-heart-max" placeholder="Max" value="99999">
                </div>
            </div>
            
            <div class="control-group">
                <h4><i class="fas fa-print"></i> Print Range</h4>
                <div class="range-input">
                    <input type="number" id="master-print-min" placeholder="Min" value="1">
                    <span>~</span>
                    <input type="number" id="master-print-max" placeholder="Max" value="1000">
                </div>
            </div>
            
            <div class="control-group">
                <h4><i class="fas fa-toggle-on"></i> Grab Status</h4>
                <button id="master-grab-toggle" class="btn" style="width: 100%;">
                    <i class="fas fa-power-off"></i> Enable All Grab
                </button>
            </div>
        </div>
    </div>

    <!-- SERVER PANELS -->
    {% for server in servers %}
    <div class="panel" data-server-id="{{ server.id }}">
        <h2>
            <span><i class="fas fa-server"></i> {{ server.name }}</span>
            <button class="delete-server"><i class="fas fa-trash"></i> Delete</button>
        </h2>
        
        <div class="input-group">
            <label><i class="fas fa-hashtag"></i> Main Channel:</label>
            <input type="text" class="channel-input" data-field="main_channel_id" 
                   value="{{ server.main_channel_id or '' }}" placeholder="Main Channel ID">
        </div>
        
        <div class="input-group">
            <label><i class="fas fa-hashtag"></i> KTB Channel:</label>
            <input type="text" class="channel-input" data-field="ktb_channel_id" 
                   value="{{ server.ktb_channel_id or '' }}" placeholder="KTB Channel ID">
        </div>
        
        {% for bot in main_bots %}
        <div class="bot-card">
            <h3>
                <i class="fas fa-robot"></i> {{ bot.name }}
                <span style="margin-left: auto; font-size: 0.8em; color: #888;">Bot {{ bot.id }}</span>
            </h3>
            
            <!-- MODE SELECTOR -->
            <div class="mode-selector">
                <button class="mode-btn {% if server['grab_mode_' + bot.id] == 1 or not server.get('grab_mode_' + bot.id) %}active{% endif %}" 
                        data-mode="1" data-bot="{{ bot.id }}">
                    <i class="fas fa-heart"></i> Mode 1: Hearts
                </button>
                <button class="mode-btn {% if server['grab_mode_' + bot.id] == 2 %}active{% endif %}" 
                        data-mode="2" data-bot="{{ bot.id }}">
                    <i class="fas fa-print"></i> Mode 2: Print
                </button>
                <button class="mode-btn {% if server['grab_mode_' + bot.id] == 3 %}active{% endif %}" 
                        data-mode="3" data-bot="{{ bot.id }}">
                    <i class="fas fa-star"></i> Mode 3: Both
                </button>
            </div>
            
            <!-- HEARTS RANGE -->
            <div class="input-group">
                <label><i class="fas fa-heart"></i> Hearts:</label>
                <div class="range-input">
                    <input type="number" class="heart-min" value="{{ server['heart_min_' + bot.id] or 50 }}" placeholder="Min">
                    <span>~</span>
                    <input type="number" class="heart-max" value="{{ server['heart_max_' + bot.id] or 99999 }}" placeholder="Max">
                </div>
            </div>
            
            <!-- PRINT RANGE -->
            <div class="input-group">
                <label><i class="fas fa-print"></i> Print:</label>
                <div class="range-input">
                    <input type="number" class="print-min" value="{{ server['print_min_' + bot.id] or 1 }}" placeholder="Min">
                    <span>~</span>
                    <input type="number" class="print-max" value="{{ server['print_max_' + bot.id] or 1000 }}" placeholder="Max">
                </div>
            </div>
            
            <!-- GRAB TOGGLE -->
            <button class="btn toggle-grab {% if server['auto_grab_enabled_' + bot.id] %}active{% endif %}" 
                    data-bot="{{ bot.id }}" style="width: 100%;">
                <i class="fas fa-power-off"></i> 
                {{ 'DISABLE GRAB' if server['auto_grab_enabled_' + bot.id] else 'ENABLE GRAB' }}
            </button>
        </div>
        {% endfor %}
    </div>
    {% endfor %}
    
    <script>
        // Uptime counter
        const startTime = {{ start_time }};
        setInterval(() => {
            const elapsed = Math.floor(Date.now() / 1000 - startTime);
            const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
            const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
            const s = (elapsed % 60).toString().padStart(2, '0');
            document.getElementById('uptime').textContent = `${h}:${m}:${s}`;
        }, 1000);

        async function post(url, data) {
            await fetch(url, { 
                method: 'POST', 
                headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify(data) 
            });
            location.reload();
        }
        
        // Add server
        document.getElementById('add-server-btn').addEventListener('click', () => {
            const name = prompt("Server Name:");
            if(name) post('/api/add_server', {name: name});
        });
        
        // Delete server
        document.querySelectorAll('.delete-server').forEach(btn => {
            btn.addEventListener('click', () => {
                if(confirm('Delete this server?')) {
                    post('/api/delete_server', {
                        server_id: btn.closest('.panel').dataset.serverId
                    });
                }
            });
        });
        
        // Update channels
        document.querySelectorAll('.channel-input').forEach(inp => {
            inp.addEventListener('change', () => {
                const sid = inp.closest('.panel').dataset.serverId;
                const field = inp.dataset.field;
                fetch('/api/update_server_field', { 
                    method: 'POST', 
                    headers: {'Content-Type': 'application/json'}, 
                    body: JSON.stringify({server_id: sid, [field]: inp.value}) 
                });
            });
        });
        
        // Mode selector
        document.querySelectorAll('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const card = btn.closest('.bot-card');
                const botId = btn.dataset.bot;
                const mode = btn.dataset.mode;
                const serverId = btn.closest('.panel').dataset.serverId;
                
                // Remove active from siblings
                card.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                
                // Save mode
                fetch('/api/update_bot_mode', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        server_id: serverId,
                        bot_id: botId,
                        mode: mode
                    })
                });
            });
        });
        
        // Toggle grab
        document.querySelectorAll('.toggle-grab').forEach(btn => {
            btn.addEventListener('click', () => {
                const card = btn.closest('.bot-card');
                const serverId = btn.closest('.panel').dataset.serverId;
                const botId = btn.dataset.bot;
                
                const heartMin = card.querySelector('.heart-min').value;
                const heartMax = card.querySelector('.heart-max').value;
                const printMin = card.querySelector('.print-min').value;
                const printMax = card.querySelector('.print-max').value;
                
                post('/api/harvest_toggle', {
                    server_id: serverId,
                    node: botId,
                    heart_min: heartMin,
                    heart_max: heartMax,
                    print_min: printMin,
                    print_max: printMax
                });
            });
        });
        
        // Sync all from master
        document.getElementById('sync-all-btn').addEventListener('click', () => {
            if(confirm('Sync all bots with Master Panel settings?')) {
                const mode = document.getElementById('master-mode').value;
                const heartMin = document.getElementById('master-heart-min').value;
                const heartMax = document.getElementById('master-heart-max').value;
                const printMin = document.getElementById('master-print-min').value;
                const printMax = document.getElementById('master-print-max').value;
                
                post('/api/sync_all', {
                    mode, heartMin, heartMax, printMin, printMax
                });
            }
        });
        
        // Master grab toggle
        document.getElementById('master-grab-toggle').addEventListener('click', () => {
            if(confirm('Toggle grab for ALL bots?')) {
                post('/api/toggle_all_grab', {});
            }
        });
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    main_bots = [{"id": str(i+1), "name": f"Main Bot {i+1}"} for i in range(len(main_tokens))]
    return render_template_string(HTML_TEMPLATE, servers=servers, main_bots=main_bots, 
                                   start_time=server_start_time)

@app.route("/api/add_server", methods=['POST'])
def api_add_server():
    name = request.json.get('name')
    if not name: return jsonify({'status': 'error'}), 400
    new_server = {"id": f"server_{uuid.uuid4().hex}", "name": name}
    main_bots_count = len([t for t in main_tokens if t.strip()])
    for i in range(main_bots_count):
        bot_num = i + 1
        new_server[f'auto_grab_enabled_{bot_num}'] = False
        new_server[f'grab_mode_{bot_num}'] = 1
        new_server[f'heart_min_{bot_num}'] = 50
        new_server[f'heart_max_{bot_num}'] = 99999
        new_server[f'print_min_{bot_num}'] = 1
        new_server[f'print_max_{bot_num}'] = 1000
    servers.append(new_server)
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/delete_server", methods=['POST'])
def api_delete_server():
    server_id = request.json.get('server_id')
    servers[:] = [s for s in servers if s.get('id') != server_id]
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/update_server_field", methods=['POST'])
def api_update_server_field():
    data = request.json
    server = next((s for s in servers if s.get('id') == data.get('server_id')), None)
    if not server: return jsonify({'status': 'error'}), 404
    for key, value in data.items():
        if key != 'server_id': server[key] = value
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/update_bot_mode", methods=['POST'])
def api_update_bot_mode():
    data = request.json
    server = next((s for s in servers if s.get('id') == data.get('server_id')), None)
    if not server: return jsonify({'status': 'error'}), 404
    bot_id = data.get('bot_id')
    mode = int(data.get('mode', 1))
    server[f'grab_mode_{bot_id}'] = mode
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/harvest_toggle", methods=['POST'])
def api_harvest_toggle():
    data = request.json
    server = next((s for s in servers if s.get('id') == data.get('server_id')), None)
    if not server: return jsonify({'status': 'error'}), 400
    node = str(data.get('node'))
    
    grab_key = f'auto_grab_enabled_{node}'
    server[grab_key] = not server.get(grab_key, False)
    
    server[f'heart_min_{node}'] = int(data.get('heart_min', 50))
    server[f'heart_max_{node}'] = int(data.get('heart_max', 99999))
    server[f'print_min_{node}'] = int(data.get('print_min', 1))
    server[f'print_max_{node}'] = int(data.get('print_max', 1000))
    
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/sync_all", methods=['POST'])
def api_sync_all():
    data = request.json
    mode = int(data.get('mode', 1))
    heart_min = int(data.get('heartMin', 50))
    heart_max = int(data.get('heartMax', 99999))
    print_min = int(data.get('printMin', 1))
    print_max = int(data.get('printMax', 1000))
    
    for server in servers:
        for i in range(len(main_tokens)):
            bot_num = i + 1
            server[f'grab_mode_{bot_num}'] = mode
            server[f'heart_min_{bot_num}'] = heart_min
            server[f'heart_max_{bot_num}'] = heart_max
            server[f'print_min_{bot_num}'] = print_min
            server[f'print_max_{bot_num}'] = print_max
    
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/toggle_all_grab", methods=['POST'])
def api_toggle_all_grab():
    # Check current state - if any bot is disabled, enable all, otherwise disable all
    any_disabled = False
    for server in servers:
        for i in range(len(main_tokens)):
            bot_num = i + 1
            if not server.get(f'auto_grab_enabled_{bot_num}', False):
                any_disabled = True
                break
    
    new_state = any_disabled
    
    for server in servers:
        for i in range(len(main_tokens)):
            bot_num = i + 1
            server[f'auto_grab_enabled_{bot_num}'] = new_state
    
    save_settings()
    return jsonify({'status': 'success'})

if __name__ == "__main__":
    print("🚀 Shadow Grabber - Premium Edition Starting...", flush=True)
    load_settings()

    for i, token in enumerate(main_tokens):
        if token.strip():
            threading.Thread(target=initialize_and_run_bot, args=(token.strip(), f"main_{i+1}", True), daemon=True).start()
    
    print("⚠️ Chế độ: GRAB ONLY - Optimized (Bot 1 đọc, các bot khác nhặt)", flush=True)

    threading.Thread(target=periodic_task, args=(1800, save_settings, "Save"), daemon=True).start()
    threading.Thread(target=periodic_task, args=(300, health_monitoring_check, "Health"), daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    from waitress import serve
    serve(app, host="0.0.0.0", port=port)
