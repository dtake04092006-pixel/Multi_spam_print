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
# <<< LOGIC NHẶT THẺ - PHIÊN BẢN MỚI (MULTI-MODE) >>>
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
    """Logic nhặt thẻ Multi-Mode: Kiểm tra tất cả mode đang bật và chọn cái tốt nhất"""
    
    channel_id = msg.channel.id
    target_server = next((s for s in servers if s.get('main_channel_id') == str(channel_id)), None)
    if not target_server: return

    auto_grab = target_server.get(f'auto_grab_enabled_{bot_num}', False)
    if not auto_grab: 
        return

    # CHỈ BOT 1 QUÉT - CÁC BOT KHÁC CHỜ
    if bot_num == 1:
        await scan_and_share_drop_info(bot, msg, channel_id)
        await asyncio.sleep(0.3)  # Delay nhỏ
    else:
        # Các bot khác chờ
        await asyncio.sleep(random.uniform(0.5, 0.8))
    
    # Lấy dữ liệu chia sẻ
    with shared_drop_info["lock"]:
        if shared_drop_info["message_id"] != msg.id:
            print(f"[GRAB | Bot {bot_num}] ⚠️ Message ID không khớp, bỏ qua.", flush=True)
            return
        
        heart_data = shared_drop_info["heart_data"]
        ocr_data = shared_drop_info["ocr_data"]
    
    # Lấy cấu hình các mode
    mode1_active = target_server.get(f'mode_1_active_{bot_num}', False) # Tim
    mode2_active = target_server.get(f'mode_2_active_{bot_num}', False) # Print
    mode3_active = target_server.get(f'mode_3_active_{bot_num}', False) # Cả hai

    heart_min = target_server.get(f'heart_min_{bot_num}', 50)
    heart_max = target_server.get(f'heart_max_{bot_num}', 99999)
    print_min = target_server.get(f'print_min_{bot_num}', 1)
    print_max = target_server.get(f'print_max_{bot_num}', 1000)
    
    candidates = [] # Danh sách các nước đi có thể (priority, emoji, delay, reason)
    # Priority: 3 (Mode 3 - Cao nhất) > 2 (Mode 2) > 1 (Mode 1)

    # --- KIỂM TRA MODE 3: CẢ TIM VÀ PRINT (Ưu tiên cao nhất) ---
    if mode3_active and heart_data and ocr_data:
        valid_cards = []
        print_dict = {idx: val for idx, val in ocr_data}
        for idx, hearts in enumerate(heart_data):
            if idx in print_dict:
                print_val = print_dict[idx]
                if (heart_min <= hearts <= heart_max) and (print_min <= print_val <= print_max):
                    valid_cards.append((idx, hearts, print_val))
        
        if valid_cards:
            best = min(valid_cards, key=lambda x: (x[2], -x[1])) # Print thấp nhất -> Tim cao nhất
            best_idx, best_hearts, best_print = best
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            candidates.append((3, emoji, 0.5, f"Mode 3 [Both] - Hearts {best_hearts} + Print #{best_print}"))

    # --- KIỂM TRA MODE 2: CHỈ PRINT ---
    if mode2_active and ocr_data:
        valid_prints = [(idx, val) for idx, val in ocr_data if print_min <= val <= print_max]
        if valid_prints:
            best_idx, best_print = min(valid_prints, key=lambda x: x[1])
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            candidates.append((2, emoji, 0.7, f"Mode 2 [Print] - Print #{best_print}"))

    # --- KIỂM TRA MODE 1: CHỈ TIM ---
    if mode1_active and heart_data:
        valid_cards = [(idx, hearts) for idx, hearts in enumerate(heart_data) if heart_min <= hearts <= heart_max]
        if valid_cards:
            best_idx, best_hearts = max(valid_cards, key=lambda x: x[1])
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            candidates.append((1, emoji, 0.3, f"Mode 1 [Heart] - Hearts {best_hearts}"))
            
    # --- QUYẾT ĐỊNH ---
    if candidates:
        # Sắp xếp theo Priority giảm dần (Mode 3 > Mode 2 > Mode 1)
        # Nếu cùng priority thì lấy cái đầu tiên tìm thấy
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        best_choice = candidates[0]
        priority, emoji, delay, reason = best_choice
        
        print(f"[GRAB | Bot {bot_num}] 🎯 Chọn: {reason} (Priority {priority})", flush=True)
        
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
    <title>Shadow OCR Premium - Multi Mode</title>
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
            padding: 20px 0;
            background: linear-gradient(135deg, #8b0000, #4b0082);
            border-radius: 15px;
            margin-bottom: 20px;
            box-shadow: 0 5px 20px rgba(139, 0, 0, 0.5);
        }
        
        .header h1 {
            font-size: 2em;
            text-shadow: 0 0 20px rgba(255, 215, 0, 0.8);
            margin-bottom: 5px;
        }
        
        .uptime { color: #ffd700; font-size: 0.9em; }
        
        .control-bar {
            display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; justify-content: center;
        }
        
        .btn {
            background: linear-gradient(135deg, #333, #555); color: white; border: none; padding: 10px 20px;
            cursor: pointer; border-radius: 6px; font-size: 0.9em; transition: all 0.3s;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
        }
        
        .btn:hover { transform: translateY(-2px); box-shadow: 0 6px 15px rgba(0, 0, 0, 0.4); }
        .btn-primary { background: linear-gradient(135deg, #006400, #008000); }
        
        .server-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); gap: 20px;
        }

        .panel {
            background: linear-gradient(135deg, #111, #1a1a1a); border: 1px solid #444; padding: 15px;
            border-radius: 12px; box-shadow: 0 5px 15px rgba(0, 0, 0, 0.5); transition: all 0.3s;
            display: flex; flex-direction: column;
        }
        
        .panel:hover { border-color: #8b0000; transform: translateY(-5px); }
        
        .panel h2 {
            border-bottom: 2px solid #8b0000; padding-bottom: 10px; margin-bottom: 15px; color: #ffd700;
            font-size: 1.2em; display: flex; justify-content: space-between; align-items: center;
        }
        
        .input-group { margin-bottom: 10px; display: flex; gap: 5px; align-items: center; }
        .input-group label { min-width: 40px; font-size: 0.8em; color: #aaa; }
        
        input {
            background: rgba(0, 0, 0, 0.6); border: 1px solid #555; color: white; padding: 8px;
            border-radius: 4px; flex: 1; width: 100%;
        }
        
        .bot-card {
            background: rgba(255, 255, 255, 0.03); padding: 10px; margin-bottom: 10px;
            border-radius: 8px; border: 1px solid #333;
        }
        
        .bot-card h3 { font-size: 1em; color: #ddd; margin-bottom: 10px; }
        
        /* New Toggle Button Style */
        .mode-selector { display: flex; gap: 5px; margin-bottom: 10px; }
        .mode-btn { 
            flex: 1; padding: 5px; font-size: 0.85em; 
            background: #222; border: 1px solid #444; border-radius: 4px; color: #666; cursor: pointer;
            transition: all 0.2s;
        }
        .mode-btn:hover { background: #333; }
        
        /* Active states for each mode */
        .mode-btn.active-1 { background: #ff4444; color: white; border-color: #ff0000; } /* Hearts */
        .mode-btn.active-2 { background: #4444ff; color: white; border-color: #0000ff; } /* Print */
        .mode-btn.active-3 { background: #ffd700; color: black; border-color: #ffd700; font-weight: bold; } /* Both */
        
        .range-input { display: flex; gap: 5px; align-items: center; }
        .range-input input { width: 50px; text-align: center; }
        
        .toggle-grab {
            width: 100%; padding: 8px; border-radius: 4px; border: none; cursor: pointer; font-weight: bold; font-size: 0.9em;
            background: #333; color: #aaa;
        }
        .toggle-grab.active { background: linear-gradient(135deg, #006400, #008000); color: white; }
        
        .delete-server { background: #8b0000; border: none; color: white; padding: 5px 10px; border-radius: 4px; cursor: pointer; font-size: 0.8em; }
    </style>
</head>
<body>
    <div class="header">
        <h1><i class="fas fa-crown"></i> Shadow OCR Multi-Mode</h1>
        <div class="uptime">⏱️ Uptime: <span id="uptime">00:00:00</span></div>
    </div>

    <div class="control-bar">
        <button id="add-server-btn" class="btn btn-primary"><i class="fas fa-plus"></i> Add Server</button>
        <button id="master-grab-toggle" class="btn" style="background: #006400;"><i class="fas fa-power-off"></i> Toggle All</button>
    </div>

    <div class="server-grid">
        {% for server in servers %}
        <div class="panel" data-server-id="{{ server.id }}">
            <h2>
                <span><i class="fas fa-server"></i> {{ server.name }}</span>
                <button class="delete-server"><i class="fas fa-trash"></i></button>
            </h2>
            
            <div class="input-group">
                <input type="text" class="channel-input" data-field="main_channel_id" value="{{ server.main_channel_id or '' }}" placeholder="Main Channel ID">
            </div>
            
            <div class="input-group">
                <input type="text" class="channel-input" data-field="ktb_channel_id" value="{{ server.ktb_channel_id or '' }}" placeholder="KTB Channel ID">
            </div>
            
            {% for bot in main_bots %}
            <div class="bot-card">
                <h3><i class="fas fa-robot"></i> {{ bot.name }}</h3>
                
                <div class="mode-selector">
                    <button class="mode-btn {{ 'active-1' if server['mode_1_active_' + bot.id] else '' }}" 
                            onclick="toggleMode(this, '1', '{{ bot.id }}', '{{ server.id }}')">
                        ❤️ Hearts
                    </button>
                    <button class="mode-btn {{ 'active-2' if server['mode_2_active_' + bot.id] else '' }}" 
                            onclick="toggleMode(this, '2', '{{ bot.id }}', '{{ server.id }}')">
                        📷 Print
                    </button>
                    <button class="mode-btn {{ 'active-3' if server['mode_3_active_' + bot.id] else '' }}" 
                            onclick="toggleMode(this, '3', '{{ bot.id }}', '{{ server.id }}')">
                        ⭐ Both
                    </button>
                </div>
                
                <div class="input-group">
                    <label>❤️</label>
                    <div class="range-input" style="flex:1">
                        <input type="number" class="heart-min" value="{{ server['heart_min_' + bot.id] or 50 }}" placeholder="Min">
                        <input type="number" class="heart-max" value="{{ server['heart_max_' + bot.id] or 99999 }}" placeholder="Max">
                    </div>
                </div>
                
                <div class="input-group">
                    <label>📷</label>
                    <div class="range-input" style="flex:1">
                        <input type="number" class="print-min" value="{{ server['print_min_' + bot.id] or 1 }}" placeholder="Min">
                        <input type="number" class="print-max" value="{{ server['print_max_' + bot.id] or 1000 }}" placeholder="Max">
                    </div>
                </div>
                
                <button class="toggle-grab {% if server['auto_grab_enabled_' + bot.id] %}active{% endif %}" data-bot="{{ bot.id }}">
                    {{ 'RUNNING' if server['auto_grab_enabled_' + bot.id] else 'STOPPED' }}
                </button>
            </div>
            {% endfor %}
        </div>
        {% endfor %}
    </div>
    
    <script>
        const startTime = {{ start_time }};
        setInterval(() => {
            const elapsed = Math.floor(Date.now() / 1000 - startTime);
            const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
            const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
            const s = (elapsed % 60).toString().padStart(2, '0');
            document.getElementById('uptime').textContent = `${h}:${m}:${s}`;
        }, 1000);

        async function post(url, data) {
            await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
            // Reload nhẹ nhàng không cần refresh cả trang nếu muốn (nhưng ở đây reload cho chắc)
            location.reload();
        }

        function toggleMode(btn, mode, botId, serverId) {
            // Hiệu ứng UI ngay lập tức
            const activeClass = 'active-' + mode;
            const isActive = btn.classList.toggle(activeClass);
            
            // Gửi API
            fetch('/api/toggle_bot_mode', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ server_id: serverId, bot_id: botId, mode: mode, active: isActive })
            });
        }
        
        document.getElementById('add-server-btn').addEventListener('click', () => {
            const name = prompt("Server Name:");
            if(name) post('/api/add_server', {name: name});
        });
        
        document.querySelectorAll('.delete-server').forEach(btn => {
            btn.addEventListener('click', () => {
                if(confirm('Delete?')) post('/api/delete_server', { server_id: btn.closest('.panel').dataset.serverId });
            });
        });
        
        document.querySelectorAll('.channel-input').forEach(inp => {
            inp.addEventListener('change', () => {
                const sid = inp.closest('.panel').dataset.serverId;
                const field = inp.dataset.field;
                fetch('/api/update_server_field', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({server_id: sid, [field]: inp.value}) });
            });
        });
        
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
                    server_id: serverId, node: botId,
                    heart_min: heartMin, heart_max: heartMax,
                    print_min: printMin, print_max: printMax
                });
            });
        });
        
        document.getElementById('master-grab-toggle').addEventListener('click', () => {
            if(confirm('Toggle ALL bots?')) post('/api/toggle_all_grab', {});
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
        # Mặc định bật Mode 1
        new_server[f'mode_1_active_{bot_num}'] = True 
        new_server[f'mode_2_active_{bot_num}'] = False
        new_server[f'mode_3_active_{bot_num}'] = False
        
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

@app.route("/api/toggle_bot_mode", methods=['POST'])
def api_toggle_bot_mode():
    data = request.json
    server = next((s for s in servers if s.get('id') == data.get('server_id')), None)
    if not server: return jsonify({'status': 'error'}), 404
    
    bot_id = data.get('bot_id')
    mode = data.get('mode')
    active = data.get('active')
    
    key = f'mode_{mode}_active_{bot_id}'
    server[key] = active
    
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

@app.route("/api/toggle_all_grab", methods=['POST'])
def api_toggle_all_grab():
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
    print("🚀 Shadow Grabber - Multi Mode Edition Starting...", flush=True)
    load_settings()

    for i, token in enumerate(main_tokens):
        if token.strip():
            threading.Thread(target=initialize_and_run_bot, args=(token.strip(), f"main_{i+1}", True), daemon=True).start()
    
    print("⚠️ Chế độ: MULTI MODE - Có thể bật nhiều mode cùng lúc", flush=True)

    threading.Thread(target=periodic_task, args=(1800, save_settings, "Save"), daemon=True).start()
    threading.Thread(target=periodic_task, args=(300, health_monitoring_check, "Health"), daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    from waitress import serve
    serve(app, host="0.0.0.0", port=port)
