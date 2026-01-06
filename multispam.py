import discord, asyncio, threading, time, os, re, requests, json, random, traceback, uuid
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
import cv2
import numpy as np
import pytesseract

# --- CẤU HÌNH OCR ---
# Trên Docker Linux, tesseract thường nằm ở đây
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
stop_events = {"reboot": threading.Event()}
server_start_time = time.time()

# --- QUẢN LÍ BOT THREAD-SAFE ---
class ThreadSafeBotManager:
    def __init__(self):
        self._bots = {}
        self._rebooting = set()
        self._lock = threading.RLock()

    def add_bot(self, bot_id, bot_data):
        with self._lock: self._bots[bot_id] = bot_data

    def remove_bot(self, bot_id):
        with self._lock:
            bot_data = self._bots.pop(bot_id, None)
            if bot_data and bot_data.get('instance'):
                asyncio.run_coroutine_threadsafe(bot_data['instance'].close(), bot_data['loop'])
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

# --- HÀM GỬI LỆNH ASYNC TỪ LUỒNG ĐỒNG BỘ ---
def send_message_from_sync(bot_id, channel_id, content):
    bot_data = bot_manager.get_bot_data(bot_id)
    if not bot_data: return
    async def _send():
        try:
            channel = bot_data['instance'].get_channel(int(channel_id))
            if channel: await channel.send(content)
        except: pass
    if bot_data['loop'].is_running():
        asyncio.run_coroutine_threadsafe(_send(), bot_data['loop'])

# --- LƯU & TẢI CÀI ĐẶT ---
def save_settings():
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    settings_data = {'servers': servers, 'bot_states': bot_states, 'last_save_time': time.time()}
    # (Giữ nguyên logic JSONBin/Local như cũ để tiết kiệm dòng code hiển thị ở đây)
    try:
        with open('backup_settings.json', 'w') as f: json.dump(settings_data, f, indent=2)
    except: pass

def load_settings():
    global servers, bot_states
    # (Giữ nguyên logic load như cũ)
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

# ==============================================================================
# <<< XỬ LÝ ẢNH (OCR) - PHẦN MỚI >>>
# ==============================================================================
def scan_image_for_prints(image_url):
    """
    Tải ảnh, cắt ảnh thành 3 hoặc 4 phần, đọc số Print ở dưới cùng.
    Trả về: List [(index_thẻ, số_print), ...]
    """
    print(f"[OCR LOG] 📥 Đang tải ảnh từ URL...", flush=True)
    try:
        resp = requests.get(image_url, timeout=3)
        if resp.status_code != 200: return []
        
        # Chuyển bytes thành ảnh OpenCV
        arr = np.asarray(bytearray(resp.content), dtype=np.uint8)
        img = cv2.imdecode(arr, -1) # -1 để giữ màu (nếu cần) hoặc 0 để grayscale
        if img is None: return []

        height, width, _ = img.shape
        # Karuta Drop thường có 3 hoặc 4 thẻ.
        # Logic đơn giản: Chia chiều rộng thành 3 hoặc 4 phần bằng nhau.
        # Nếu chiều rộng > 1000px thường là 3-4 thẻ.
        num_cards = 3 
        if width > 1300: num_cards = 4 # Dự đoán sơ bộ
        
        card_width = width // num_cards
        results = []

        print(f"[OCR LOG] 🖼️ Ảnh size {width}x{height}. Chia làm {num_cards} cột.", flush=True)

        for i in range(num_cards):
            # Cắt lấy 1 thẻ
            x_start = i * card_width
            x_end = (i + 1) * card_width
            
            # Cắt lấy phần đáy (nơi chứa Print/Gen) - Khoảng 18% dưới cùng
            y_start = int(height * 0.85) 
            crop_img = img[y_start:height, x_start:x_end]

            # Xử lý ảnh để rõ chữ hơn (Grayscale -> Threshold)
            gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
            # Threshold: Chuyển các pixel sáng thành trắng, tối thành đen để tách chữ
            _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV) 

            # Dùng Tesseract đọc
            # config: chỉ đọc số (digits) để tăng tốc và độ chính xác
            custom_config = r'--oem 3 --psm 6 outputbase digits'
            text = pytesseract.image_to_string(thresh, config=custom_config)
            
            # Lọc lấy số đầu tiên tìm thấy
            # Text thường có dạng: "79371 - 1" hoặc "1234 · 2"
            numbers = re.findall(r'\d+', text)
            
            if numbers:
                # Số print thường là số lớn (ví dụ > 5), số edition là số nhỏ (1,2,3)
                # Lấy số dài nhất hoặc số đầu tiên
                print_num = int(numbers[0])
                results.append((i, print_num))
                print(f"[OCR LOG] 👁️ Thẻ {i+1}: Đọc được Print = {print_num} (Raw: '{text.strip()}')", flush=True)
            else:
                 print(f"[OCR LOG] 👁️ Thẻ {i+1}: Không đọc được số. (Raw: '{text.strip()}')", flush=True)

        return results

    except Exception as e:
        print(f"[OCR LOG] ❌ Lỗi xử lý ảnh: {e}", flush=True)
        return []

# ==============================================================================
# <<< LOGIC NHẶT THẺ CẢI TIẾN >>>
# ==============================================================================
async def handle_grab(bot, msg, bot_num):
    channel_id = msg.channel.id
    target_server = next((s for s in servers if s.get('main_channel_id') == str(channel_id)), None)
    if not target_server: return

    bot_id_str = f'main_{bot_num}'
    # Config cơ bản
    auto_grab = target_server.get(f'auto_grab_enabled_{bot_num}', False)
    # Config OCR
    ocr_enabled = target_server.get(f'ocr_enabled_{bot_num}', False)
    print_max_limit = target_server.get(f'print_threshold_{bot_num}', 1000) # Mặc định nhặt nếu print < 1000

    if not auto_grab: return

    final_choice = None # (emoji, delay, reason)

    # --- BƯỚC 1: CHECK TIM (NHANH) ---
    # (Giữ logic cũ vì nó nhanh, không tốn resource)
    start_time = time.monotonic()
    try:
        channel = bot.get_channel(int(channel_id))
        if channel:
            # Chờ 1 chút để embed load xong hoàn toàn
            await asyncio.sleep(0.5) 
            async for msg_item in channel.history(limit=5):
                if msg_item.author.id == int(karibbit_id) and msg_item.id > msg.id:
                    if not msg_item.embeds: continue
                    desc = msg_item.embeds[0].description
                    if not desc or '♡' not in desc: continue

                    lines = desc.split('\n')[:4]
                    heart_numbers = [int(re.search(r'♡(\d+)', line).group(1)) if re.search(r'♡(\d+)', line) else 0 for line in lines]
                    
                    min_h = target_server.get(f'heart_threshold_{bot_num}', 50)
                    max_h = target_server.get(f'max_heart_threshold_{bot_num}', 99999)
                    
                    valid_cards = [(idx, hearts) for idx, hearts in enumerate(heart_numbers) if min_h <= hearts <= max_h]
                    
                    if valid_cards:
                        best_idx, best_hearts = max(valid_cards, key=lambda x: x[1])
                        emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
                        final_choice = (emoji, 0.8, f"Hearts {best_hearts}")
                        break
    except Exception as e:
        print(f"[GRAB] Lỗi check tim: {e}", flush=True)

    # --- BƯỚC 2: CHECK PRINT (OCR - CHẬM HƠN CHÚT) ---
    # Chỉ chạy nếu chưa tìm được thẻ theo tim VÀ tính năng OCR được bật
    if not final_choice and ocr_enabled and msg.embeds and msg.embeds[0].image:
        image_url = msg.embeds[0].image.url
        # Chạy OCR trong thread riêng để không block bot
        print(f"[GRAB] 📷 Bắt đầu quét ảnh tìm Low Print (Max: {print_max_limit})...", flush=True)
        
        loop = asyncio.get_event_loop()
        # Chạy hàm sync scan_image_for_prints trong executor
        ocr_results = await loop.run_in_executor(None, scan_image_for_prints, image_url)
        
        # Tìm thẻ có Print thấp nhất và nhỏ hơn giới hạn
        valid_prints = [x for x in ocr_results if x[1] <= print_max_limit]
        
        if valid_prints:
            # Lấy thẻ có print nhỏ nhất
            best_print_idx, best_print_val = min(valid_prints, key=lambda x: x[1])
            if best_print_idx < 4:
                emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_print_idx]
                final_choice = (emoji, 0.5, f"Low Print #{best_print_val}")
                print(f"[GRAB] ✅ TÌM THẤY PRINT NGON! Index: {best_print_idx+1}, Value: {best_print_val}", flush=True)

    # --- THỰC HIỆN GRAB ---
    if final_choice:
        emoji, delay, reason = final_choice
        print(f"[GRAB | Bot {bot_num}] 🎯 Quyết định nhặt {emoji}. Lý do: {reason}", flush=True)
        
        async def grab_action():
            await asyncio.sleep(delay)
            try:
                target_msg = await msg.channel.fetch_message(msg.id)
                await target_msg.add_reaction(emoji)
                # Spam nhẹ nếu cần
                ktb_id = target_server.get('ktb_channel_id')
                if ktb_id:
                    ktb = bot.get_channel(int(ktb_id))
                    if ktb: await ktb.send("kt fs")
            except Exception as e:
                print(f"[GRAB] Lỗi react: {e}", flush=True)
        
        asyncio.create_task(grab_action())


# --- KHỞI TẠO BOT (Phần còn lại giống code cũ) ---
def initialize_and_run_bot(token, bot_id_str, is_main, ready_event=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = discord.Client(self_bot=True)
    try: bot_identifier = int(bot_id_str.split('_')[1])
    except: bot_identifier = 99

    @bot.event
    async def on_ready():
        print(f"[Bot] ✅ Login: {bot.user.name} ({bot_id_str})", flush=True)
        if ready_event: ready_event.set()

    @bot.event
    async def on_message(msg):
        if not is_main: return
        try:
            if msg.author.id == int(karuta_id) and "dropping" in msg.content.lower():
                await handle_grab(bot, msg, bot_identifier)
        except Exception as e:
            print(f"[Err] {e}", flush=True)

    try:
        bot_manager.add_bot(bot_id_str, {'instance': bot, 'loop': loop})
        loop.run_until_complete(bot.start(token))
    except Exception as e:
        print(f"[Bot] ❌ Crash {bot_id_str}: {e}", flush=True)
    finally:
        bot_manager.remove_bot(bot_id_str)
        loop.close()

# --- WEB SERVER (UI Updated for OCR) ---
app = Flask(__name__)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shadow OCR Control</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background: #0a0a0a; color: #f0f0f0; font-family: monospace; padding: 20px; }
        .panel { background: #111; border: 1px solid #333; padding: 20px; margin-bottom: 20px; border-radius: 8px; }
        .btn { background: #333; color: white; border: none; padding: 8px 15px; cursor: pointer; border-radius: 4px; }
        .btn:hover { background: #444; }
        .input-group { margin-bottom: 10px; display: flex; gap: 10px; align-items: center; }
        input { background: #000; border: 1px solid #444; color: white; padding: 8px; border-radius: 4px; }
        h2 { border-bottom: 2px solid #8b0000; padding-bottom: 10px; color: #f0f0f0; }
        .ocr-badge { background: #00008b; padding: 2px 6px; border-radius: 4px; font-size: 0.8em; }
    </style>
</head>
<body>
    <h1>Shadow Network - OCR Edition</h1>
    {% for server in servers %}
    <div class="panel" data-server-id="{{ server.id }}">
        <h2>{{ server.name }}</h2>
        {% for bot in main_bots %}
        <div style="background: #1a1a1a; padding: 10px; margin-bottom: 10px; border-radius: 4px;">
            <h3>{{ bot.name }}</h3>
            <div class="input-group">
                <label>Hearts:</label>
                <input type="number" class="heart-min" value="{{ server['heart_threshold_' + bot.id] or 50 }}" placeholder="Min">
                <input type="number" class="heart-max" value="{{ server['max_heart_threshold_' + bot.id] or 99999 }}" placeholder="Max">
                <button class="btn toggle-grab" data-bot="{{ bot.id }}">{{ 'DISABLE GRAB' if server['auto_grab_enabled_' + bot.id] else 'ENABLE GRAB' }}</button>
            </div>
            <div class="input-group" style="border-top: 1px dashed #444; padding-top: 10px;">
                <label><i class="fas fa-eye"></i> OCR Print:</label>
                <input type="number" class="print-limit" value="{{ server['print_threshold_' + bot.id] or 1000 }}" placeholder="Max Print to Grab">
                <button class="btn toggle-ocr" data-bot="{{ bot.id }}" style="background: {{ '#006400' if server['ocr_enabled_' + bot.id] else '#333' }};">
                    {{ 'OCR: ON' if server['ocr_enabled_' + bot.id] else 'OCR: OFF' }}
                </button>
            </div>
        </div>
        {% endfor %}
    </div>
    {% endfor %}
    
    <script>
        // JS xử lý API (Tương tự phiên bản cũ, thêm phần OCR)
        document.querySelectorAll('.toggle-ocr').forEach(btn => {
            btn.addEventListener('click', async () => {
                const serverId = btn.closest('.panel').dataset.serverId;
                const botId = btn.dataset.bot;
                const printLimit = btn.parentElement.querySelector('.print-limit').value;
                
                await fetch('/api/ocr_toggle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({server_id: serverId, node: botId, limit: printLimit})
                });
                location.reload();
            });
        });
        // (Giữ các script xử lý toggle-grab cũ của bạn ở đây)
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    main_bots = [{"id": str(i+1), "name": f"Main {i+1}"} for i in range(len(main_tokens))]
    return render_template_string(HTML_TEMPLATE, servers=servers, main_bots=main_bots)

@app.route("/api/ocr_toggle", methods=['POST'])
def api_ocr_toggle():
    data = request.json
    server = next((s for s in servers if s['id'] == data['server_id']), None)
    if server:
        node = str(data['node'])
        key_enable = f'ocr_enabled_{node}'
        key_limit = f'print_threshold_{node}'
        
        server[key_enable] = not server.get(key_enable, False)
        server[key_limit] = int(data.get('limit', 1000))
        save_settings()
    return jsonify({'status': 'success'})

# ... (Các API cũ giữ nguyên) ...

if __name__ == "__main__":
    load_settings()
    # Khởi chạy bot main
    for i, token in enumerate(main_tokens):
        if token.strip():
            threading.Thread(target=initialize_and_run_bot, args=(token.strip(), f"main_{i+1}", True), daemon=True).start()
    
    # Khởi chạy bot sub (nếu có)
    
    port = int(os.environ.get("PORT", 10000))
    from waitress import serve
    serve(app, host="0.0.0.0", port=port)
