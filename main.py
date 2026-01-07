import discord, asyncio, threading, time, os, re, requests, json, random, traceback, uuid
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
import numpy as np
import pytesseract
from PIL import Image, ImageOps, ImageEnhance
import io

# --- CẤU HÌNH OCR ---
# Lưu ý: Nếu chạy trên Windows, bạn có thể cần sửa đường dẫn này thành đường dẫn cài Tesseract trên máy bạn
# Ví dụ: r'C:\Program Files\Tesseract-OCR\tesseract.exe'
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
# <<< XỬ LÝ ẢNH (OCR) - FIX LỖI SSL & RETRY >>>
# ==============================================================================
def scan_image_for_prints(image_url):
    print(f"[OCR LOG] 📥 Đang tải ảnh từ URL...", flush=True)
    
    # --- ĐOẠN FIX: Thử lại 3 lần nếu mạng lỗi ---
    img_content = None
    for attempt in range(3):
        try:
            # Tăng timeout lên 10s để đỡ bị ngắt khi mạng lag
            resp = requests.get(image_url, timeout=10) 
            if resp.status_code == 200:
                img_content = resp.content
                break # Tải thành công thì thoát vòng lặp
        except Exception as e:
            print(f"[OCR LOG] ⚠️ Lần {attempt+1} lỗi tải ảnh: {e}. Đang thử lại...", flush=True)
            time.sleep(1.5) # Nghỉ 1.5s rồi thử lại
    
    if img_content is None:
        print(f"[OCR LOG] ❌ Đã thử 3 lần nhưng không tải được ảnh.", flush=True)
        return []
    # --------------------------------------------

    try:
        img = Image.open(io.BytesIO(img_content))
        width, height = img.size
        
        num_cards = 3 
        if width > 1000: num_cards = 4
        
        card_width = width // num_cards
        results = []

        print(f"[OCR LOG] 🖼️ Ảnh size {width}x{height}. Chia làm {num_cards} cột.", flush=True)

        for i in range(num_cards):
            left = i * card_width
            right = (i + 1) * card_width
            
            # Cắt phần dưới cùng chứa Print
            print_crop_top = int(height * 0.86) 
            crop_img = img.crop((left, print_crop_top, right, height))

            # Xử lý ảnh
            crop_img = crop_img.convert('L') 
            enhancer = ImageEnhance.Contrast(crop_img)
            crop_img = enhancer.enhance(2.0) 
            crop_img = ImageOps.invert(crop_img)

            # OCR whitelist số
            custom_config = r'--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789'
            text = pytesseract.image_to_string(crop_img, config=custom_config)
            
            # Regex tìm số
            numbers = re.findall(r'\d+', text)
            
            print_num = 0
            edition_num = 0
            
            if numbers:
                # TRƯỜNG HỢP 1: Tách chuẩn 2 số
                if len(numbers) >= 2:
                    print_num = int(numbers[0])
                    edition_num = int(numbers[1])
                    print(f"[OCR LOG] 👁️ Thẻ {i+1}: Tách chuẩn -> Print: {print_num} | Ed: {edition_num}", flush=True)

                # TRƯỜNG HỢP 2: Dính chùm -> Cắt số cuối
                elif len(numbers) == 1:
                    raw_str = numbers[0]
                    if len(raw_str) > 1:
                        print_num = int(raw_str[:-1]) 
                        edition_num = int(raw_str[-1]) 
                        print(f"[OCR LOG] 👁️ Thẻ {i+1}: Dính chùm '{raw_str}' -> Cắt Print: {print_num} | Ed: {edition_num}", flush=True)
                    else:
                        print_num = int(raw_str)
                        print(f"[OCR LOG] 👁️ Thẻ {i+1}: Chỉ thấy 1 số -> Print: {print_num}", flush=True)
                
                if print_num > 0:
                    results.append((i, print_num))
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
    """
    Logic nhặt thẻ Multi-Mode (ĐÃ FIX: TỰ ĐỘNG CẬP NHẬT DATA CŨ)
    """
    
    channel_id = msg.channel.id
    # Tìm server (thêm strip() để xóa khoảng trắng thừa nếu có)
    target_server = next((s for s in servers if str(s.get('main_channel_id')).strip() == str(channel_id)), None)
    
    if not target_server:
        print(f"[DEBUG FAIL] ❌ Bot {bot_num}: Thấy Drop nhưng chưa add Channel ID {channel_id} vào Web.", flush=True)
        return

    auto_grab = target_server.get(f'auto_grab_enabled_{bot_num}', False)
    if not auto_grab: 
        # Nếu Bot 1 (Bot chính) bị tắt, in log cảnh báo để biết
        if bot_num == 1:
            print(f"[DEBUG FAIL] ⛔ Bot 1 thấy Drop nhưng nút trạng thái đang là STOPPED. Hãy bật lên RUNNING.", flush=True)
        return

    # CHỈ BOT 1 QUÉT - CÁC BOT KHÁC CHỜ
    if bot_num == 1:
        await scan_and_share_drop_info(bot, msg, channel_id)
        await asyncio.sleep(0.3)
    else:
        await asyncio.sleep(random.uniform(0.5, 0.8))
    
    # Lấy dữ liệu chia sẻ
    with shared_drop_info["lock"]:
        if shared_drop_info["message_id"] != msg.id:
            return
        heart_data = shared_drop_info["heart_data"]
        ocr_data = shared_drop_info["ocr_data"]
    
    # --- ĐOẠN FIX QUAN TRỌNG: TỰ ĐỘNG CHUYỂN ĐỔI DATA CŨ ---
    mode1_active = target_server.get(f'mode_1_active_{bot_num}')
    mode2_active = target_server.get(f'mode_2_active_{bot_num}')
    mode3_active = target_server.get(f'mode_3_active_{bot_num}')

    # Nếu cả 3 cái đều chưa có (do dùng file save cũ), TỰ ĐỘNG BẬT MODE 1
    if mode1_active is None and mode2_active is None and mode3_active is None:
        print(f"[AUTO-FIX] ⚠️ Bot {bot_num}: Phát hiện Data cũ. Tự động kích hoạt Mode 1 (Tim) để chạy ngay.", flush=True)
        mode1_active = True
        # Lưu ngược lại vào bộ nhớ để lần sau không cần fix nữa
        target_server[f'mode_1_active_{bot_num}'] = True
    else:
        # Ép kiểu về boolean để tránh lỗi None
        mode1_active = bool(mode1_active)
        mode2_active = bool(mode2_active)
        mode3_active = bool(mode3_active)
    # --------------------------------------------------------

    heart_min = target_server.get(f'heart_min_{bot_num}', 50)
    heart_max = target_server.get(f'heart_max_{bot_num}', 99999)
    print_min = target_server.get(f'print_min_{bot_num}', 1)
    print_max = target_server.get(f'print_max_{bot_num}', 1000)
    
    candidates = [] 

    # --- MODE 3: BOTH ---
    if mode3_active and heart_data and ocr_data:
        m3_h_min = target_server.get(f'm3_heart_min_{bot_num}', 50)
        m3_h_max = target_server.get(f'm3_heart_max_{bot_num}', 99999)
        m3_p_min = target_server.get(f'm3_print_min_{bot_num}', 1)
        m3_p_max = target_server.get(f'm3_print_max_{bot_num}', 1000)

        valid_cards = []
        print_dict = {idx: val for idx, val in ocr_data}
        for idx, hearts in enumerate(heart_data):
            if idx in print_dict:
                print_val = print_dict[idx]
                if (m3_h_min <= hearts <= m3_h_max) and (m3_p_min <= print_val <= m3_p_max):
                    valid_cards.append((idx, hearts, print_val))
        
        if valid_cards:
            best = min(valid_cards, key=lambda x: (x[2], -x[1])) 
            best_idx, best_hearts, best_print = best
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            candidates.append((3, emoji, 0.5, f"Mode 3 [Both] - H:{best_hearts} P:#{best_print}"))
            
    # --- MODE 2: PRINT ---
    if mode2_active and ocr_data:
        valid_prints = [(idx, val) for idx, val in ocr_data if print_min <= val <= print_max]
        if valid_prints:
            best_idx, best_print = min(valid_prints, key=lambda x: x[1])
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            candidates.append((2, emoji, 0.7, f"Mode 2 [Print] - Print #{best_print}"))

    # --- MODE 1: HEART ---
    if mode1_active and heart_data:
        valid_cards = [(idx, hearts) for idx, hearts in enumerate(heart_data) if heart_min <= hearts <= heart_max]
        if valid_cards:
            best_idx, best_hearts = max(valid_cards, key=lambda x: x[1])
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
            candidates.append((1, emoji, 0.3, f"Mode 1 [Heart] - Hearts {best_hearts}"))
            
    # --- QUYẾT ĐỊNH ---
    if candidates:
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
    else:
        # Log debug để biết nếu không có thẻ nào thỏa mãn
        active_modes = []
        if mode1_active: active_modes.append("Mode 1")
        if mode2_active: active_modes.append("Mode 2")
        if mode3_active: active_modes.append("Mode 3")
        print(f"[DEBUG] Bot {bot_num}: Đã quét xong nhưng không nhặt. (Modes bật: {active_modes}, Tim: {heart_data})", flush=True)

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
    <title>Shadow OCR Master Control</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0f0f13; color: #f0f0f0; font-family: sans-serif; padding: 20px; }
        
        /* HEADER & MASTER PANEL */
        .header { text-align: center; margin-bottom: 20px; }
        .header h1 { color: #ffd700; text-shadow: 0 0 10px rgba(255, 215, 0, 0.5); }
        
        .master-panel {
            background: linear-gradient(135deg, #2c003e, #000000);
            border: 2px solid #ffd700;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 30px;
            box-shadow: 0 0 20px rgba(255, 215, 0, 0.2);
        }
        .master-title {
            text-align: center; color: #ffd700; font-weight: bold; margin-bottom: 15px; text-transform: uppercase;
            border-bottom: 1px solid #444; padding-bottom: 10px; font-size: 1.2em;
        }
        
        .master-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px;
        }
        
        .master-bot-card {
            background: rgba(255,255,255,0.05); padding: 10px; border-radius: 8px; border: 1px solid #555;
        }
        .master-bot-name { color: #00ff00; font-weight: bold; margin-bottom: 5px; text-align: center; display: block; }
        
        /* SHARED STYLES */
        .btn { padding: 8px 15px; border: none; border-radius: 4px; cursor: pointer; color: white; font-weight: bold; }
        .btn-sync { background: #ffd700; color: #000; width: 100%; margin-top: 15px; font-size: 1.1em; transition: 0.3s; }
        .btn-sync:hover { background: #ffea00; box-shadow: 0 0 15px #ffd700; }
        
        .btn-add { background: #006400; margin-bottom: 20px; }
        .btn-del { background: #8b0000; float: right; font-size: 0.8em; padding: 2px 8px; }

        .server-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
        .panel { background: #1a1a1a; border: 1px solid #333; padding: 15px; border-radius: 8px; }
        .panel h2 { color: #aaa; font-size: 1.1em; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px; }

        input { background: #333; border: 1px solid #555; color: white; padding: 5px; width: 100%; border-radius: 4px; }
        .input-group { margin-bottom: 8px; }
        
        .bot-card { background: #222; padding: 10px; margin-top: 10px; border-radius: 6px; border: 1px solid #444; }
        
        /* MODE BUTTONS */
        .mode-selector { display: flex; gap: 5px; margin-bottom: 5px; }
        .mode-btn { flex: 1; padding: 5px; background: #333; border: 1px solid #555; color: #888; cursor: pointer; font-size: 0.8em; }
        
        .mode-btn.active-1 { background: #ff4444; color: white; border-color: red; }
        .mode-btn.active-2 { background: #4444ff; color: white; border-color: blue; }
        .mode-btn.active-3 { background: #ffd700; color: black; border-color: gold; font-weight: bold; }

        .range-row { display: flex; gap: 5px; align-items: center; margin-bottom: 5px; }
        .range-row label { width: 20px; font-size: 0.9em; }
        
        .m3-config { background: rgba(255,215,0,0.05); padding: 5px; border: 1px solid #555; margin-top: 5px; border-radius: 4px; }
        .m3-label { font-size: 0.7em; color: #ffd700; text-align: center; }

        .toggle-grab { width: 100%; padding: 6px; margin-top: 5px; background: #333; color: #666; border: none; cursor: pointer; }
        .toggle-grab.active { background: #006400; color: white; }
    </style>
</head>
<body>
    <div class="header">
        <h1>👑 SHADOW MASTER CONTROL</h1>
        <div style="color: #888; font-size: 0.9em;">Uptime: <span id="uptime">Loading...</span></div>
    </div>

    <div class="master-panel">
        <div class="master-title"><i class="fas fa-sliders-h"></i> BẢNG ĐIỀU KHIỂN TỔNG (MASTER)</div>
        <div class="master-grid">
            {% for bot in main_bots %}
            <div class="master-bot-card" id="master-bot-{{ bot.id }}">
                <span class="master-bot-name">{{ bot.name }}</span>
                
                <div class="mode-selector">
                    <button class="mode-btn" onclick="toggleMasterMode(this, '1')">❤️ Tim</button>
                    <button class="mode-btn" onclick="toggleMasterMode(this, '2')">📷 Print</button>
                    <button class="mode-btn" onclick="toggleMasterMode(this, '3')">⭐ Both</button>
                </div>
                <input type="hidden" class="master-mode-1" value="false">
                <input type="hidden" class="master-mode-2" value="false">
                <input type="hidden" class="master-mode-3" value="false">

                <div class="range-row">
                    <label>❤️</label>
                    <input type="number" class="master-h-min" value="50" placeholder="Min">
                    <input type="number" class="master-h-max" value="99999" placeholder="Max">
                </div>
                <div class="range-row">
                    <label>📷</label>
                    <input type="number" class="master-p-min" value="1" placeholder="Min">
                    <input type="number" class="master-p-max" value="1000" placeholder="Max">
                </div>

                <div class="m3-config">
                    <div class="m3-label">MODE 3 CONFIG</div>
                    <div class="range-row">
                        <input type="number" class="master-m3-h-min" value="50" placeholder="❤️ Min">
                        <input type="number" class="master-m3-h-max" value="99999" placeholder="Max">
                    </div>
                    <div class="range-row">
                        <input type="number" class="master-m3-p-min" value="1" placeholder="📷 Min">
                        <input type="number" class="master-m3-p-max" value="1000" placeholder="Max">
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
        <button class="btn btn-sync" onclick="syncAll()"><i class="fas fa-sync-alt"></i> ĐỒNG BỘ CẤU HÌNH XUỐNG TẤT CẢ SERVER</button>
    </div>

    <div style="text-align:center; margin-bottom: 20px;">
        <button id="add-server-btn" class="btn btn-add"><i class="fas fa-plus"></i> Add New Server</button>
        <button id="master-grab-toggle" class="btn" style="background: #006400;"><i class="fas fa-power-off"></i> Toggle All RUNNING</button>
    </div>

    <div class="server-grid">
        {% for server in servers %}
        <div class="panel" data-server-id="{{ server.id }}">
            <h2>
                <i class="fas fa-server"></i> {{ server.name }}
                <button class="btn btn-del delete-server"><i class="fas fa-trash"></i></button>
            </h2>
            <div class="input-group"><input type="text" class="channel-input" data-field="main_channel_id" value="{{ server.main_channel_id or '' }}" placeholder="Main Channel ID"></div>
            <div class="input-group"><input type="text" class="channel-input" data-field="ktb_channel_id" value="{{ server.ktb_channel_id or '' }}" placeholder="KTB Channel ID"></div>
            
            {% for bot in main_bots %}
            <div class="bot-card">
                <div style="font-weight:bold; color:#ddd; font-size:0.9em; margin-bottom:5px;">{{ bot.name }}</div>
                
                <div class="mode-selector">
                    <button class="mode-btn {{ 'active-1' if server['mode_1_active_' + bot.id] else '' }}" onclick="toggleMode(this, '1', '{{ bot.id }}', '{{ server.id }}')">❤️</button>
                    <button class="mode-btn {{ 'active-2' if server['mode_2_active_' + bot.id] else '' }}" onclick="toggleMode(this, '2', '{{ bot.id }}', '{{ server.id }}')">📷</button>
                    <button class="mode-btn {{ 'active-3' if server['mode_3_active_' + bot.id] else '' }}" onclick="toggleMode(this, '3', '{{ bot.id }}', '{{ server.id }}')">⭐</button>
                </div>
                
                <div class="range-row"><label>❤️</label> <input type="number" class="heart-min" value="{{ server['heart_min_' + bot.id] or 50 }}"> <input type="number" class="heart-max" value="{{ server['heart_max_' + bot.id] or 99999 }}"></div>
                <div class="range-row"><label>📷</label> <input type="number" class="print-min" value="{{ server['print_min_' + bot.id] or 1 }}"> <input type="number" class="print-max" value="{{ server['print_max_' + bot.id] or 1000 }}"></div>

                <div class="m3-config">
                    <div class="range-row"><input type="number" class="m3-h-min" value="{{ server['m3_heart_min_' + bot.id] or 50 }}"> <input type="number" class="m3-h-max" value="{{ server['m3_heart_max_' + bot.id] or 99999 }}"></div>
                    <div class="range-row"><input type="number" class="m3-p-min" value="{{ server['m3_print_min_' + bot.id] or 1 }}"> <input type="number" class="m3-p-max" value="{{ server['m3_print_max_' + bot.id] or 1000 }}"></div>
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
            location.reload();
        }

        // Logic cho Master Panel
        function toggleMasterMode(btn, mode) {
            btn.classList.toggle('active-' + mode);
            const parent = btn.closest('.master-bot-card');
            const input = parent.querySelector('.master-mode-' + mode);
            input.value = btn.classList.contains('active-' + mode) ? "true" : "false";
        }

        function syncAll() {
            if(!confirm('Bạn có chắc muốn áp dụng cấu hình này cho TẤT CẢ SERVER không?')) return;
            
            const botsConfig = [];
            document.querySelectorAll('.master-bot-card').forEach(card => {
                const botId = card.id.replace('master-bot-', '');
                botsConfig.push({
                    id: botId,
                    mode1: card.querySelector('.master-mode-1').value === "true",
                    mode2: card.querySelector('.master-mode-2').value === "true",
                    mode3: card.querySelector('.master-mode-3').value === "true",
                    h_min: card.querySelector('.master-h-min').value,
                    h_max: card.querySelector('.master-h-max').value,
                    p_min: card.querySelector('.master-p-min').value,
                    p_max: card.querySelector('.master-p-max').value,
                    m3_h_min: card.querySelector('.master-m3-h-min').value,
                    m3_h_max: card.querySelector('.master-m3-h-max').value,
                    m3_p_min: card.querySelector('.master-m3-p-min').value,
                    m3_p_max: card.querySelector('.master-m3-p-max').value
                });
            });

            post('/api/sync_master_config', { bots: botsConfig });
        }

        // Logic cũ
        function toggleMode(btn, mode, botId, serverId) {
            const activeClass = 'active-' + mode;
            const isActive = btn.classList.toggle(activeClass);
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
                // Lấy data hiện tại để gửi (dù logic server lưu độc lập nhưng cứ gửi cho đủ)
                const heartMin = card.querySelector('.heart-min').value;
                const heartMax = card.querySelector('.heart-max').value;
                const printMin = card.querySelector('.print-min').value;
                const printMax = card.querySelector('.print-max').value;
                const m3_h_min = card.querySelector('.m3-h-min').value;
                const m3_h_max = card.querySelector('.m3-h-max').value;
                const m3_p_min = card.querySelector('.m3-p-min').value;
                const m3_p_max = card.querySelector('.m3-p-max').value;
                
                post('/api/harvest_toggle', {
                    server_id: serverId, node: botId,
                    heart_min: heartMin, heart_max: heartMax,
                    print_min: printMin, print_max: printMax,
                    m3_heart_min: m3_h_min, m3_heart_max: m3_h_max,
                    m3_print_min: m3_p_min, m3_print_max: m3_p_max
                });
            });
        });

        document.getElementById('master-grab-toggle').addEventListener('click', () => {
            if(confirm('Toggle ALL bots RUNNING?')) post('/api/toggle_all_grab', {});
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
    server[f'm3_heart_min_{node}'] = int(data.get('m3_heart_min', 50))
    server[f'm3_heart_max_{node}'] = int(data.get('m3_heart_max', 99999))
    server[f'm3_print_min_{node}'] = int(data.get('m3_print_min', 1))
    server[f'm3_print_max_{node}'] = int(data.get('m3_print_max', 1000))
    
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
@app.route("/api/sync_master_config", methods=['POST'])
def api_sync_master_config():
    data = request.json
    bots_config = data.get('bots', [])
    
    # Duyệt qua từng server
    for server in servers:
        # Duyệt qua từng cấu hình bot từ Master Panel
        for config in bots_config:
            bot_id = config['id']
            
            # Đồng bộ Mode (True/False)
            server[f'mode_1_active_{bot_id}'] = config['mode1']
            server[f'mode_2_active_{bot_id}'] = config['mode2']
            server[f'mode_3_active_{bot_id}'] = config['mode3']
            
            # Đồng bộ Tim/Print cơ bản
            server[f'heart_min_{bot_id}'] = int(config['h_min'])
            server[f'heart_max_{bot_id}'] = int(config['h_max'])
            server[f'print_min_{bot_id}'] = int(config['p_min'])
            server[f'print_max_{bot_id}'] = int(config['p_max'])
            
            # Đồng bộ cấu hình Mode 3 riêng
            server[f'm3_heart_min_{bot_id}'] = int(config['m3_h_min'])
            server[f'm3_heart_max_{bot_id}'] = int(config['m3_h_max'])
            server[f'm3_print_min_{bot_id}'] = int(config['m3_p_min'])
            server[f'm3_print_max_{bot_id}'] = int(config['m3_p_max'])
            
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
