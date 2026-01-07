import discord, asyncio, threading, time, os, re, requests, json, random, traceback, uuid
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
import numpy as np
import pytesseract
from PIL import Image, ImageOps, ImageEnhance # <--- Thư viện xử lý ảnh mới
import io # <--- Để xử lý ảnh trên RAM

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
                    # Chỉ đóng bot nếu loop còn đang chạy
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
# <<< XỬ LÝ ẢNH (OCR) - PHIÊN BẢN PIL (CHUẨN LOGIC KARUTA SNIPER) >>>
# ==============================================================================
def scan_image_for_prints(image_url):
    print(f"[OCR LOG] 📥 Đang tải ảnh từ URL...", flush=True)
    try:
        resp = requests.get(image_url, timeout=5)
        if resp.status_code != 200: return []
        
        # Đọc ảnh trực tiếp từ RAM (không lưu file)
        img = Image.open(io.BytesIO(resp.content))
        
        width, height = img.size
        
        # Logic xác định số lượng thẻ dựa trên chiều rộng ảnh
        # Ảnh 3 thẻ thường rộng ~900px, 4 thẻ ~1200px
        num_cards = 3 
        if width > 1000: num_cards = 4
        
        card_width = width // num_cards
        results = []

        print(f"[OCR LOG] 🖼️ Ảnh size {width}x{height}. Chia làm {num_cards} cột (PIL Mode).", flush=True)

        for i in range(num_cards):
            # 1. Xác định tọa độ cắt thẻ
            left = i * card_width
            right = (i + 1) * card_width
            top = 0
            bottom = height
            
            # 2. Cắt vùng chứa số Print (Phần đáy thẻ)
            # Theo kinh nghiệm và code tham khảo, print nằm ở khoảng 13-15% dưới cùng
            print_crop_top = int(height * 0.86) # Lấy từ 86% đổ xuống
            
            # Cắt lấy phần Print của từng thẻ
            # crop((left, top, right, bottom))
            crop_img = img.crop((left, print_crop_top, right, bottom))

            # 3. Xử lý ảnh để rõ số (Pre-processing)
            # Chuyển sang thang độ xám (Grayscale)
            crop_img = crop_img.convert('L')
            
            # Tăng độ tương phản (Contrast)
            enhancer = ImageEnhance.Contrast(crop_img)
            crop_img = enhancer.enhance(2.0) # Tăng gấp đôi độ tương phản
            
            # Nghịch đảo màu (Invert) - Số trắng nền đen -> Số đen nền trắng (Tesseract thích cái này)
            crop_img = ImageOps.invert(crop_img)

            # 4. Config Tesseract chuyên dụng cho số (Giống code tham khảo)
            # --psm 7: Coi ảnh là 1 dòng văn bản duy nhất (Rất quan trọng cho số Print)
            # whitelist: Chỉ cho phép đọc số
            custom_config = r'--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789'
            
            text = pytesseract.image_to_string(crop_img, config=custom_config)
            
            # Lọc lấy số (Đôi khi nó đọc ra cả số Edition bên cạnh, ví dụ: 79371 1)
            # Ta lấy số lớn nhất vì Print luôn > Edition
            numbers = re.findall(r'\d+', text)
            
            if numbers:
                # Chuyển list string thành list int
                int_numbers = [int(n) for n in numbers]
                # Số Print thường là số lớn nhất trong đống đó
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
# <<< LOGIC NHẶT THẺ (DEBUG CHI TIẾT + FIX LAG ẢNH) >>>
# ==============================================================================
async def handle_grab(bot, msg, bot_num):
    channel_id = msg.channel.id
    target_server = next((s for s in servers if s.get('main_channel_id') == str(channel_id)), None)
    
    # 1. Kiểm tra cấu hình server
    if not target_server: 
        print(f"[DEBUG] ❌ Bot {bot_num}: Không tìm thấy cấu hình server cho kênh này.", flush=True)
        return

    bot_id_str = f'main_{bot_num}'
    auto_grab = target_server.get(f'auto_grab_enabled_{bot_num}', False)
    ocr_enabled = target_server.get(f'ocr_enabled_{bot_num}', False)
    print_max_limit = target_server.get(f'print_threshold_{bot_num}', 1000)

    # 2. Kiểm tra nút bật/tắt trên Web
    # Lưu ý: Trên web nút phải hiện chữ "DISABLE GRAB" (màu đỏ/xám) thì biến này mới là True
    if not auto_grab: 
        print(f"[DEBUG] ⛔ Bot {bot_num}: AutoGrab đang TẮT. Hãy bấm nút trên Web!", flush=True)
        return

    # 3. [QUAN TRỌNG] CHỜ ẢNH LOAD (FIX LAG)
    # Karuta thường mất 0.5s - 1s để load ảnh sau khi chat
    print(f"[DEBUG] ⏳ Bot {bot_num}: Đang chờ 1 giây để Karuta tải ảnh...", flush=True)
    await asyncio.sleep(1.0) 

    try:
        # Tải lại tin nhắn để lấy Embed mới nhất
        msg = await msg.channel.fetch_message(msg.id)
    except Exception as e:
        print(f"[DEBUG] ❌ Lỗi khi tải lại tin nhắn: {e}", flush=True)
        return

    final_choice = None 

    # --- ƯU TIÊN 1: OCR (QUÉT ẢNH) ---
    if ocr_enabled:
        if not msg.embeds:
            print(f"[DEBUG] ❌ Bot {bot_num}: Tin nhắn không có Embed (Ảnh lỗi hoặc mạng lag).", flush=True)
        elif not msg.embeds[0].image:
            print(f"[DEBUG] ❌ Bot {bot_num}: Embed có, nhưng không có URL ảnh.", flush=True)
        else:
            image_url = msg.embeds[0].image.url
            print(f"[GRAB] 📷 Bot {bot_num}: Đang quét ảnh... (Max Print: {print_max_limit})", flush=True)
            
            loop = asyncio.get_event_loop()
            ocr_results = await loop.run_in_executor(None, scan_image_for_prints, image_url)
            
            # Lọc thẻ có print nhỏ hơn giới hạn
            valid_prints = [x for x in ocr_results if x[1] <= print_max_limit]
            
            if valid_prints:
                best_print_idx, best_print_val = min(valid_prints, key=lambda x: x[1])
                if best_print_idx < 4:
                    emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_print_idx]
                    final_choice = (emoji, 0.5, f"Low Print #{best_print_val}")
                    print(f"[GRAB] ✅ Bot {bot_num}: TÌM THẤY PRINT NGON! Index: {best_print_idx+1}, Value: {best_print_val}", flush=True)
            else:
                # Nếu đọc được nhưng không có thẻ nào thỏa mãn
                print(f"[DEBUG] 📉 Bot {bot_num}: Quét xong. Không có thẻ nào dưới {print_max_limit}.", flush=True)

    # --- ƯU TIÊN 2: CHECK TIM (CHỈ CHẠY NẾU OCR KHÔNG RA) ---
    if not final_choice:
        try:
            if msg.embeds and msg.embeds[0].description and '♡' in msg.embeds[0].description:
                desc = msg.embeds[0].description
                lines = desc.split('\n')[:4]
                heart_numbers = [int(re.search(r'♡(\d+)', line).group(1)) if re.search(r'♡(\d+)', line) else 0 for line in lines]
                
                min_h = target_server.get(f'heart_threshold_{bot_num}', 50)
                max_h = target_server.get(f'max_heart_threshold_{bot_num}', 99999)
                
                valid_cards = [(idx, hearts) for idx, hearts in enumerate(heart_numbers) if min_h <= hearts <= max_h]
                
                if valid_cards:
                    best_idx, best_hearts = max(valid_cards, key=lambda x: x[1])
                    emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][best_idx]
                    final_choice = (emoji, 0.8, f"Hearts {best_hearts}")
                    print(f"[GRAB] ❤️ Bot {bot_num}: Nhặt theo Tim ({best_hearts})", flush=True)
        except Exception as e:
            print(f"[GRAB] Lỗi check tim: {e}", flush=True)

    # --- THỰC HIỆN GRAB ---
    if final_choice:
        emoji, delay, reason = final_choice
        print(f"[GRAB | Bot {bot_num}] 🎯 QUYẾT ĐỊNH NHẶT {emoji}. Lý do: {reason}", flush=True)
        
        async def grab_action():
            await asyncio.sleep(delay)
            try:
                await msg.add_reaction(emoji)
                # KTB logic...
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
    bot = discord.Client(self_bot=True)
    
    try: 
        bot_identifier = int(bot_id_str.split('_')[1])
    except: 
        bot_identifier = 99

    @bot.event
    async def on_ready():
        print(f"[Bot] ✅ Login: {bot.user.name} ({bot_id_str})", flush=True)
        if ready_event: ready_event.set()

    @bot.event
    async def on_message(msg):
        if not is_main: return
        
        # --- [BƯỚC LỌC QUAN TRỌNG NHẤT] ---
        # Kiểm tra xem kênh hiện tại (msg.channel.id) có nằm trong danh sách web không
        # Lưu ý: So sánh string vì trong config lưu dạng string
        target_server = next((s for s in servers if s.get('main_channel_id') == str(msg.channel.id)), None)
        
        # Nếu KHÔNG tìm thấy config cho kênh này -> DỪNG NGAY LẬP TỨC (Không in log rác)
        if not target_server:
            return

        # --- NẾU ĐÚNG KÊNH CẦN THEO DÕI THÌ MỚI CHẠY TIẾP ---
        
        # In log debug CHỈ KHI đúng kênh quan tâm
        if "dropping" in msg.content.lower():
            print(f"[DEBUG] 👀 Bot {bot_id_str} thấy Drop tại kênh ĐÚNG {msg.channel.id}", flush=True)

        try:
            if (msg.author.id == int(karuta_id) or msg.author.id == int(karibbit_id)) and "dropping" in msg.content.lower():
                print(f"[DEBUG] ✅ PHÁT HIỆN DROP CHUẨN! Đang xử lý...", flush=True)
                await handle_grab(bot, msg, bot_identifier)
        except Exception as e:
            print(f"[Err] {e}", flush=True)
            traceback.print_exc()
    try:
        # Thêm bot vào manager TRƯỚC khi start
        bot_manager.add_bot(bot_id_str, {'instance': bot, 'loop': loop})
        loop.run_until_complete(bot.start(token))
    except KeyboardInterrupt:
        print(f"[Bot] ⚠️ KeyboardInterrupt cho {bot_id_str}", flush=True)
    except Exception as e:
        print(f"[Bot] ❌ Crash {bot_id_str}: {e}", flush=True)
        traceback.print_exc()
    finally:
        # Cleanup an toàn
        try:
            if not loop.is_closed():
                # Đóng bot trước khi đóng loop
                if not bot.is_closed():
                    loop.run_until_complete(bot.close())
                # Hủy tất cả tasks còn lại
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                # Đợi tasks bị hủy hoàn tất
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
        except Exception as e:
            print(f"[Bot] ⚠️ Lỗi cleanup {bot_id_str}: {e}", flush=True)
        finally:
            # Xóa khỏi manager
            bot_manager.remove_bot(bot_id_str)

# --- WEB SERVER (UI) ---
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
    <div style="margin-bottom: 20px;">
         <button id="add-server-btn" class="btn" style="background: #006400;"><i class="fas fa-plus"></i> Add Server</button>
    </div>
    {% for server in servers %}
    <div class="panel" data-server-id="{{ server.id }}">
        <h2>{{ server.name }} <button class="btn delete-server" style="float:right; background:#8b0000; font-size:0.8em;">X</button></h2>
        <div class="input-group">
            <label>Channels:</label>
            <input type="text" class="channel-input" data-field="main_channel_id" value="{{ server.main_channel_id or '' }}" placeholder="Main Channel ID">
            <input type="text" class="channel-input" data-field="ktb_channel_id" value="{{ server.ktb_channel_id or '' }}" placeholder="KTB Channel ID">
        </div>
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
        async function post(url, data) {
            await fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
            location.reload();
        }
        document.getElementById('add-server-btn').addEventListener('click', () => {
            const name = prompt("Server Name:");
            if(name) post('/api/add_server', {name: name});
        });
        document.querySelectorAll('.delete-server').forEach(btn => {
            btn.addEventListener('click', () => {
                if(confirm('Delete?')) post('/api/delete_server', {server_id: btn.closest('.panel').dataset.serverId});
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
                const p = btn.closest('.panel');
                const min = btn.parentElement.querySelector('.heart-min').value;
                const max = btn.parentElement.querySelector('.heart-max').value;
                post('/api/harvest_toggle', {server_id: p.dataset.serverId, node: btn.dataset.bot, threshold: min, max_threshold: max});
            });
        });
        document.querySelectorAll('.toggle-ocr').forEach(btn => {
            btn.addEventListener('click', () => {
                const limit = btn.parentElement.querySelector('.print-limit').value;
                post('/api/ocr_toggle', {server_id: btn.closest('.panel').dataset.serverId, node: btn.dataset.bot, limit: limit});
            });
        });
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

@app.route("/api/add_server", methods=['POST'])
def api_add_server():
    name = request.json.get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Tên server là bắt buộc.'}), 400
    new_server = {"id": f"server_{uuid.uuid4().hex}", "name": name}
    main_bots_count = len([t for t in main_tokens if t.strip()])
    for i in range(main_bots_count):
        bot_num = i + 1
        new_server[f'auto_grab_enabled_{bot_num}'] = False
        new_server[f'heart_threshold_{bot_num}'] = 50
        new_server[f'max_heart_threshold_{bot_num}'] = 99999
        new_server[f'ocr_enabled_{bot_num}'] = False
        new_server[f'print_threshold_{bot_num}'] = 1000
    servers.append(new_server)
    save_settings()
    return jsonify({'status': 'success', 'message': f'✅ Server "{name}" đã được thêm.', 'reload': True})

@app.route("/api/delete_server", methods=['POST'])
def api_delete_server():
    server_id = request.json.get('server_id')
    servers[:] = [s for s in servers if s.get('id') != server_id]
    save_settings()
    return jsonify({'status': 'success', 'message': f'🗑️ Server đã được xóa.', 'reload': True})

def find_server(server_id): return next((s for s in servers if s.get('id') == server_id), None)

@app.route("/api/update_server_field", methods=['POST'])
def api_update_server_field():
    data = request.json
    server = find_server(data.get('server_id'))
    if not server: return jsonify({'status': 'error', 'message': 'Không tìm thấy server.'}), 404
    for key, value in data.items():
        if key != 'server_id': server[key] = value
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/harvest_toggle", methods=['POST'])
def api_harvest_toggle():
    data = request.json
    server, node_str = find_server(data.get('server_id')), data.get('node')
    if not server or not node_str: return jsonify({'status': 'error'}), 400
    node = str(node_str)
    grab_key = f'auto_grab_enabled_{node}'
    server[grab_key] = not server.get(grab_key, False)
    try:
        server[f'heart_threshold_{node}'] = int(data.get('threshold', 50))
        server[f'max_heart_threshold_{node}'] = int(data.get('max_threshold', 99999))
    except: pass
    save_settings()
    return jsonify({'status': 'success'})

@app.route("/api/save_settings", methods=['POST'])
def api_save_settings(): save_settings(); return jsonify({'status': 'success'})

if __name__ == "__main__":
    print("🚀 Shadow Grabber - OCR Edition Starting...", flush=True)
    load_settings()

    # CHỈ KHỞI CHẠY BOT CHÍNH (Bot Nhặt)
    for i, token in enumerate(main_tokens):
        if token.strip():
            threading.Thread(target=initialize_and_run_bot, args=(token.strip(), f"main_{i+1}", True), daemon=True).start()
    
    print("⚠️ Chế độ: CHỈ NHẶT (GRAB ONLY) - Đã tắt Spam Sub-bots", flush=True)

    threading.Thread(target=periodic_task, args=(1800, save_settings, "Save"), daemon=True).start()
    threading.Thread(target=periodic_task, args=(300, health_monitoring_check, "Health"), daemon=True).start()
    
    port = int(os.environ.get("PORT", 10000))
    from waitress import serve
    serve(app, host="0.0.0.0", port=port)
