import discord, asyncio, threading, time, os, re, requests, json, random, traceback, uuid
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
import numpy as np
import pytesseract
from PIL import Image, ImageOps, ImageEnhance
import io

# --- CẤU HÌNH OCR ---
# Chỉnh lại đường dẫn nếu cần thiết (trên Windows thường là C:\Program Files\Tesseract-OCR\tesseract.exe)
pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'

load_dotenv()

# --- CẤU HÌNH CƠ BẢN ---
main_tokens = os.getenv("MAIN_TOKENS", "").split(",")
tokens = os.getenv("TOKENS", "").split(",")
karuta_id, karibbit_id = "646937666251915264", "1311684840462225440"
BOT_NAMES = ["xsyx", "sofa", "dont", "ayaya", "owo", "astra", "singo", "dia pox", "clam", "rambo", "domixi", "dogi", "sicula", "mo turn", "jan taru", "kio sama"]
acc_names = [f"Bot-{i:02d}" for i in range(1, 21)]

# --- BIẾN TRẠNG THÁI ---
servers = []
bot_states = {
    "health_stats": {},
}
server_start_time = time.time()

# --- SHARED MEMORY (ĐỂ ĐỒNG BỘ BOT) ---
shared_drop_info = {
    "heart_data": None,
    "ocr_data": None,
    "message_id": None,
    "timestamp": 0,
    "lock": threading.Lock()
}

# --- QUẢN LÝ BOT ---
class ThreadSafeBotManager:
    def __init__(self):
        self._bots = {}
        self._lock = threading.RLock()

    def add_bot(self, bot_id, bot_data):
        with self._lock: self._bots[bot_id] = bot_data

    def remove_bot(self, bot_id):
        with self._lock:
            bot_data = self._bots.pop(bot_id, None)
            if bot_data and bot_data.get('instance'):
                try:
                    loop = bot_data.get('loop')
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(bot_data['instance'].close(), loop)
                except: pass

    def get_all_bots_data(self):
        with self._lock: return list(self._bots.items())

bot_manager = ThreadSafeBotManager()

# --- LƯU & TẢI CÀI ĐẶT (QUAN TRỌNG: MIGRATION DATA) ---
def ensure_server_structure(server, bot_count):
    """Hàm này đảm bảo server luôn có đủ key mới nhất để tránh lỗi logic"""
    for i in range(bot_count):
        node = i + 1
        # Các key mặc định cần phải có
        defaults = {
            f'auto_grab_enabled_{node}': False,
            # Mode 1 (Hearts)
            f'mode_1_active_{node}': False,
            f'm1_heart_min_{node}': 50, f'm1_heart_max_{node}': 99999,
            # Mode 2 (Print)
            f'mode_2_active_{node}': False,
            f'm2_print_min_{node}': 1, f'm2_print_max_{node}': 1000,
            # Mode 3 (Both)
            f'mode_3_active_{node}': False,
            f'm3_heart_min_{node}': 50, f'm3_heart_max_{node}': 99999,
            f'm3_print_min_{node}': 1, f'm3_print_max_{node}': 1000,
        }
        for k, v in defaults.items():
            if k not in server:
                server[k] = v # Điền giá trị mặc định nếu thiếu

def save_settings():
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    settings_data = {'servers': servers, 'last_save_time': time.time()}
    if api_key and bin_id:
        try:
            requests.put(f"https://api.jsonbin.io/v3/b/{bin_id}", 
                         json=settings_data, 
                         headers={'Content-Type': 'application/json', 'X-Master-Key': api_key}, 
                         timeout=10)
        except: pass
    try:
        with open('backup_settings.json', 'w') as f: json.dump(settings_data, f, indent=2)
    except: pass

def load_settings():
    global servers
    loaded = False
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    
    # 1. Thử tải từ JsonBin
    if api_key and bin_id:
        try:
            req = requests.get(f"https://api.jsonbin.io/v3/b/{bin_id}/latest", headers={'X-Master-Key': api_key}, timeout=10)
            if req.status_code == 200:
                data = req.json().get("record", {})
                servers.extend(data.get('servers', []))
                loaded = True
        except: pass

    # 2. Nếu thất bại, tải từ file local
    if not loaded:
        try:
            with open('backup_settings.json', 'r') as f:
                data = json.load(f)
                servers.extend(data.get('servers', []))
        except: pass

    # 3. CHẠY MIGRATION DATA
    bot_count = len([t for t in main_tokens if t.strip()])
    for s in servers:
        ensure_server_structure(s, bot_count)
    print(f"[SYSTEM] ✅ Đã tải và đồng bộ cấu trúc cho {len(servers)} servers.", flush=True)

# --- XỬ LÝ ẢNH (OCR) ---
def scan_image_for_prints(image_url):
    try:
        resp = requests.get(image_url, timeout=5)
        if resp.status_code != 200: return []
        img = Image.open(io.BytesIO(resp.content))
        width, height = img.size
        num_cards = 4 if width > 1000 else 3
        card_width = width // num_cards
        results = []
        
        for i in range(num_cards):
            left, right = i * card_width, (i + 1) * card_width
            crop_img = img.crop((left, int(height * 0.86), right, height))
            crop_img = ImageOps.invert(ImageEnhance.Contrast(crop_img.convert('L')).enhance(2.0))
            text = pytesseract.image_to_string(crop_img, config=r'--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789')
            numbers = re.findall(r'\d+', text)
            if numbers: results.append((i, max(map(int, numbers))))
        return results
    except Exception as e:
        print(f"[OCR ERROR] {e}", flush=True)
        return []

# --- CORE LOGIC: SCAN & GRAB ---
async def scan_and_share_drop_info(msg):
    """Bot 1 quét và chia sẻ thông tin"""
    with shared_drop_info["lock"]:
        shared_drop_info["heart_data"] = None
        shared_drop_info["ocr_data"] = None
        shared_drop_info["message_id"] = msg.id
        shared_drop_info["timestamp"] = time.time()

    print(f"[SCAN] 🔍 Bắt đầu quét...", flush=True)
    
    # Reload message để lấy embed đầy đủ
    try: msg = await msg.channel.fetch_message(msg.id)
    except: return

    # 1. Quét Tim
    heart_data = None
    try:
        async for hist_msg in msg.channel.history(limit=5):
            if hist_msg.author.id == int(karibbit_id) and hist_msg.created_at > msg.created_at:
                if hist_msg.embeds and '♡' in (hist_msg.embeds[0].description or ''):
                    lines = hist_msg.embeds[0].description.split('\n')[:4]
                    heart_data = [int(re.search(r'♡(\d+)', l).group(1)) if re.search(r'♡(\d+)', l) else 0 for l in lines]
                    print(f"[SCAN] ❤️ Tim: {heart_data}", flush=True)
                    break
    except: pass

    # 2. Quét Ảnh (Print)
    ocr_data = None
    img_url = msg.embeds[0].image.url if msg.embeds and msg.embeds[0].image else (msg.attachments[0].url if msg.attachments else None)
    if img_url:
        print(f"[SCAN] 📷 OCR Image...", flush=True)
        ocr_data = await asyncio.get_event_loop().run_in_executor(None, scan_image_for_prints, img_url)
        print(f"[SCAN] 👁️ Print: {ocr_data}", flush=True)

    with shared_drop_info["lock"]:
        shared_drop_info["heart_data"] = heart_data
        shared_drop_info["ocr_data"] = ocr_data

async def handle_grab(bot, msg, bot_num):
    # Tìm server config
    server = next((s for s in servers if s.get('main_channel_id') == str(msg.channel.id)), None)
    if not server:
        # Debug log nếu không tìm thấy server (Lý do phổ biến khiến không log gì cả)
        # print(f"[DEBUG] Msg tại kênh {msg.channel.id} nhưng không khớp config server nào.", flush=True)
        return

    # Check master toggle
    if not server.get(f'auto_grab_enabled_{bot_num}', False):
        return

    # Bot 1 chịu trách nhiệm Scan
    if bot_num == 1:
        await scan_and_share_drop_info(msg)
        await asyncio.sleep(0.2)
    else:
        await asyncio.sleep(random.uniform(0.4, 0.7))

    # Lấy dữ liệu đã scan
    with shared_drop_info["lock"]:
        if shared_drop_info["message_id"] != msg.id: return # Data cũ hoặc chưa có
        heart_data = shared_drop_info["heart_data"]
        ocr_data = shared_drop_info["ocr_data"]

    # --- LOGIC CHỌN THẺ ĐỘC LẬP ---
    candidates = []

    # 1. CHECK MODE 3 (BOTH) - Ưu tiên cao nhất
    if server.get(f'mode_3_active_{bot_num}') and heart_data and ocr_data:
        h_min, h_max = server.get(f'm3_heart_min_{bot_num}', 0), server.get(f'm3_heart_max_{bot_num}', 99999)
        p_min, p_max = server.get(f'm3_print_min_{bot_num}', 0), server.get(f'm3_print_max_{bot_num}', 1000)
        
        print_map = {idx: val for idx, val in ocr_data}
        valid_both = []
        for idx, hearts in enumerate(heart_data):
            if idx in print_map:
                pmap = print_map[idx]
                if (h_min <= hearts <= h_max) and (p_min <= pmap <= p_max):
                    valid_both.append((idx, hearts, pmap))
        
        if valid_both:
            # Chọn Print nhỏ nhất, nếu trùng chọn Tim to nhất
            best = min(valid_both, key=lambda x: (x[2], -x[1]))
            candidates.append((3, best[0], 0.5, f"Mode 3 (H:{best[1]} P:{best[2]})"))

    # 2. CHECK MODE 2 (PRINT ONLY)
    if server.get(f'mode_2_active_{bot_num}') and ocr_data:
        p_min, p_max = server.get(f'm2_print_min_{bot_num}', 0), server.get(f'm2_print_max_{bot_num}', 1000)
        valid_prints = [x for x in ocr_data if p_min <= x[1] <= p_max]
        if valid_prints:
            best = min(valid_prints, key=lambda x: x[1])
            candidates.append((2, best[0], 0.6, f"Mode 2 (Print:{best[1]})"))

    # 3. CHECK MODE 1 (HEART ONLY)
    if server.get(f'mode_1_active_{bot_num}') and heart_data:
        h_min, h_max = server.get(f'm1_heart_min_{bot_num}', 0), server.get(f'm1_heart_max_{bot_num}', 99999)
        valid_hearts = [(i, h) for i, h in enumerate(heart_data) if h_min <= h <= h_max]
        if valid_hearts:
            best = max(valid_hearts, key=lambda x: x[1])
            candidates.append((1, best[0], 0.3, f"Mode 1 (Heart:{best[1]})"))

    # QUYẾT ĐỊNH CUỐI CÙNG
    if candidates:
        # Sort theo Priority (3 > 2 > 1)
        candidates.sort(key=lambda x: x[0], reverse=True)
        prio, idx, delay, reason = candidates[0]
        emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][idx]
        
        print(f"[GRAB] 🤖 Bot {bot_num}: Nhặt {emoji} | Lý do: {reason}", flush=True)
        
        async def do_react():
            await asyncio.sleep(delay)
            try:
                await msg.add_reaction(emoji)
                if server.get('ktb_channel_id'):
                    ch = bot.get_channel(int(server['ktb_channel_id']))
                    if ch: await ch.send("kt fs")
            except Exception as e: print(f"[REACT FAIL] {e}", flush=True)
        asyncio.create_task(do_react())

# --- BOT MAIN ---
def run_discord_bot(token, bot_id_str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = discord.Client(self_bot=True)
    
    try: bot_num = int(bot_id_str.split('_')[1])
    except: bot_num = 1

    @bot.event
    async def on_ready():
        print(f"[READY] ✅ {bot.user} ({bot_id_str}) online!", flush=True)

    @bot.event
    async def on_message(msg):
        # Log thô để debug xem bot có nhận tin nhắn không
        if "dropping" in msg.content.lower():
             print(f"[DEBUG] 📨 Bot {bot_num} thấy chữ 'dropping' tại {msg.channel.id}", flush=True)

        if msg.author.id in [int(karuta_id), int(karibbit_id)] and "dropping" in msg.content.lower():
            await handle_grab(bot, msg, bot_num)

    try:
        bot_manager.add_bot(bot_id_str, {'instance': bot, 'loop': loop})
        loop.run_until_complete(bot.start(token))
    except Exception as e:
        print(f"[CRASH] {bot_id_str}: {e}", flush=True)
    finally:
        bot_manager.remove_bot(bot_id_str)

# --- FLASK UI (PREMIUM RESTORED) ---
app = Flask(__name__)
HTML_UI = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shadow OCR Premium v2</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root { --bg: #09090b; --card: #18181b; --border: #27272a; --accent: #8b5cf6; --text: #e4e4e7; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', sans-serif; }
        body { background: var(--bg); color: var(--text); padding: 20px; min-height: 100vh; }
        
        .header { text-align: center; margin-bottom: 30px; text-transform: uppercase; letter-spacing: 2px; }
        .header h1 { background: linear-gradient(45deg, #ff0055, #00ddff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        
        .controls { display: flex; justify-content: center; gap: 10px; margin-bottom: 30px; }
        .btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; transition: 0.2s; color: white; }
        .btn-add { background: #10b981; } .btn-save { background: #3b82f6; } .btn-toggle { background: #f59e0b; }
        .btn:hover { opacity: 0.9; transform: translateY(-2px); }

        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 20px; }
        
        .server-panel { 
            background: var(--card); border: 1px solid var(--border); border-radius: 12px; 
            padding: 20px; position: relative; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }
        .server-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
        .server-header h2 { color: var(--accent); font-size: 1.2rem; }
        
        .input-row { display: flex; gap: 10px; margin-bottom: 15px; }
        .input-field { 
            background: #000; border: 1px solid #333; color: white; padding: 8px; 
            border-radius: 4px; width: 100%; outline: none; transition: 0.2s;
        }
        .input-field:focus { border-color: var(--accent); }

        .bot-card { background: #000; border-radius: 8px; padding: 15px; margin-top: 15px; border: 1px solid #333; }
        .bot-name { color: #aaa; font-size: 0.9rem; margin-bottom: 10px; font-weight: bold; text-transform: uppercase; }

        /* MODE STYLING */
        .mode-block { 
            display: flex; align-items: center; gap: 10px; padding: 8px; 
            border-radius: 6px; margin-bottom: 8px; background: #111; border: 1px solid #222; 
        }
        .mode-toggle { 
            width: 40px; height: 35px; border: 1px solid #444; background: #222; 
            color: #666; border-radius: 4px; cursor: pointer; display: grid; place-items: center; flex-shrink: 0;
        }
        .mode-toggle.active { color: white; border-color: currentColor; }
        .mode-1.active { background: #991b1b; border-color: #ef4444; } /* Red */
        .mode-2.active { background: #1e3a8a; border-color: #3b82f6; } /* Blue */
        .mode-3.active { background: #854d0e; border-color: #eab308; } /* Yellow */

        .mode-inputs { display: flex; gap: 5px; width: 100%; }
        .mini-input { width: 100%; text-align: center; background: #222; border: 1px solid #333; color: #ddd; padding: 5px; border-radius: 4px; }
        
        /* Layout riêng cho Mode 3 để gọn */
        .mode-3-layout { display: flex; flex-direction: column; width: 100%; gap: 4px; }
        .row-m3 { display: flex; gap: 5px; align-items: center; }
        .label-m3 { width: 20px; font-size: 0.8rem; color: #777; }

        .run-btn { 
            width: 100%; padding: 10px; margin-top: 10px; border: none; border-radius: 6px; 
            font-weight: bold; cursor: pointer; background: #333; color: #666;
        }
        .run-btn.active { background: linear-gradient(90deg, #10b981, #059669); color: white; box-shadow: 0 0 10px rgba(16, 185, 129, 0.3); }

        .del-btn { background: #ef4444; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="header">
        <h1><i class="fas fa-meteor"></i> Shadow OCR V2</h1>
    </div>

    <div class="controls">
        <button class="btn btn-add" onclick="api('add_server', {name: prompt('Server Name:')})"><i class="fas fa-plus"></i> Add Server</button>
        <button class="btn btn-toggle" onclick="api('toggle_all_grab', {})"><i class="fas fa-power-off"></i> Toggle All</button>
    </div>

    <div class="grid">
        {% for server in servers %}
        <div class="server-panel" data-id="{{ server.id }}">
            <div class="server-header">
                <h2>{{ server.name }}</h2>
                <button class="del-btn" onclick="if(confirm('Delete?')) api('delete_server', {server_id: '{{server.id}}'})"><i class="fas fa-trash"></i></button>
            </div>
            
            <div class="input-row">
                <input class="input-field" placeholder="Main Channel ID" value="{{ server.main_channel_id or '' }}" onchange="update(this, 'main_channel_id')">
                <input class="input-field" placeholder="KTB Channel ID" value="{{ server.ktb_channel_id or '' }}" onchange="update(this, 'ktb_channel_id')">
            </div>

            {% for bot in main_bots %}
            <div class="bot-card" data-bot="{{ bot.id }}">
                <div class="bot-name"><i class="fas fa-robot"></i> {{ bot.name }}</div>

                <div class="mode-block">
                    <div class="mode-toggle mode-1 {{ 'active' if server['mode_1_active_' + bot.id] else '' }}" onclick="toggleMode(this)">❤️</div>
                    <div class="mode-inputs">
                        <input type="number" class="mini-input m1-min" placeholder="Min" value="{{ server['m1_heart_min_' + bot.id] }}">
                        <input type="number" class="mini-input m1-max" placeholder="Max" value="{{ server['m1_heart_max_' + bot.id] }}">
                    </div>
                </div>

                <div class="mode-block">
                    <div class="mode-toggle mode-2 {{ 'active' if server['mode_2_active_' + bot.id] else '' }}" onclick="toggleMode(this)">📷</div>
                    <div class="mode-inputs">
                        <input type="number" class="mini-input m2-min" placeholder="Min" value="{{ server['m2_print_min_' + bot.id] }}">
                        <input type="number" class="mini-input m2-max" placeholder="Max" value="{{ server['m2_print_max_' + bot.id] }}">
                    </div>
                </div>

                <div class="mode-block" style="align-items: flex-start;">
                    <div class="mode-toggle mode-3 {{ 'active' if server['mode_3_active_' + bot.id] else '' }}" onclick="toggleMode(this)" style="height: 65px">⭐</div>
                    <div class="mode-3-layout">
                        <div class="row-m3">
                            <span class="label-m3">❤️</span>
                            <input type="number" class="mini-input m3-h-min" placeholder="H-Min" value="{{ server['m3_heart_min_' + bot.id] }}">
                            <input type="number" class="mini-input m3-h-max" placeholder="H-Max" value="{{ server['m3_heart_max_' + bot.id] }}">
                        </div>
                        <div class="row-m3">
                            <span class="label-m3">📷</span>
                            <input type="number" class="mini-input m3-p-min" placeholder="P-Min" value="{{ server['m3_print_min_' + bot.id] }}">
                            <input type="number" class="mini-input m3-p-max" placeholder="P-Max" value="{{ server['m3_print_max_' + bot.id] }}">
                        </div>
                    </div>
                </div>

                <button class="run-btn {{ 'active' if server['auto_grab_enabled_' + bot.id] else '' }}" onclick="saveAndToggle(this)">
                    {{ 'RUNNING' if server['auto_grab_enabled_' + bot.id] else 'STOPPED (Click to Save & Run)' }}
                </button>
            </div>
            {% endfor %}
        </div>
        {% endfor %}
    </div>

    <script>
        async function api(endpoint, data) {
            await fetch('/api/' + endpoint, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
            location.reload();
        }

        function update(el, field) {
            const sid = el.closest('.server-panel').dataset.id;
            api('update_server_field', {server_id: sid, [field]: el.value});
        }

        function toggleMode(el) {
            el.classList.toggle('active');
            // Visual toggle only, real save happens on "Run" button or can be added here if prefer instant save
        }

        function saveAndToggle(btn) {
            const card = btn.closest('.bot-card');
            const sid = btn.closest('.server-panel').dataset.id;
            const bot = card.dataset.bot;
            
            const data = {
                server_id: sid, node: bot,
                // Get Active States
                m1_active: card.querySelector('.mode-1').classList.contains('active'),
                m2_active: card.querySelector('.mode-2').classList.contains('active'),
                m3_active: card.querySelector('.mode-3').classList.contains('active'),
                // Get Values
                m1_min: card.querySelector('.m1-min').value, m1_max: card.querySelector('.m1-max').value,
                m2_min: card.querySelector('.m2-min').value, m2_max: card.querySelector('.m2-max').value,
                m3_h_min: card.querySelector('.m3-h-min').value, m3_h_max: card.querySelector('.m3-h-max').value,
                m3_p_min: card.querySelector('.m3-p-min').value, m3_p_max: card.querySelector('.m3-p-max').value
            };
            api('harvest_save_toggle', data);
        }
    </script>
</body>
</html>
"""

# --- ROUTES ---
@app.route("/")
def index():
    main_bots = [{"id": str(i+1), "name": f"Main Bot {i+1}"} for i in range(len(main_tokens))]
    return render_template_string(HTML_UI, servers=servers, main_bots=main_bots)

@app.route("/api/harvest_save_toggle", methods=['POST'])
def handle_save_toggle():
    d = request.json
    srv = next((s for s in servers if s['id'] == d['server_id']), None)
    if not srv: return jsonify({}), 404
    
    n = d['node']
    # Toggle Running
    srv[f'auto_grab_enabled_{n}'] = not srv.get(f'auto_grab_enabled_{n}', False)
    
    # Save Active States
    srv[f'mode_1_active_{n}'] = d['m1_active']
    srv[f'mode_2_active_{n}'] = d['m2_active']
    srv[f'mode_3_active_{n}'] = d['m3_active']
    
    # Save Values (Convert to int)
    def v(key, def_val): return int(d.get(key) or def_val)
    srv[f'm1_heart_min_{n}'] = v('m1_min', 50)
    srv[f'm1_heart_max_{n}'] = v('m1_max', 99999)
    srv[f'm2_print_min_{n}'] = v('m2_min', 1)
    srv[f'm2_print_max_{n}'] = v('m2_max', 1000)
    srv[f'm3_heart_min_{n}'] = v('m3_h_min', 50)
    srv[f'm3_heart_max_{n}'] = v('m3_h_max', 99999)
    srv[f'm3_print_min_{n}'] = v('m3_p_min', 1)
    srv[f'm3_print_max_{n}'] = v('m3_p_max', 1000)
    
    save_settings()
    return jsonify({'status': 'ok'})

@app.route("/api/add_server", methods=['POST'])
def add_srv():
    servers.append({"id": uuid.uuid4().hex, "name": request.json['name']})
    # Chạy migration ngay cho server mới
    ensure_server_structure(servers[-1], len(main_tokens))
    save_settings()
    return jsonify({})

@app.route("/api/delete_server", methods=['POST'])
def del_srv():
    global servers
    servers = [s for s in servers if s['id'] != request.json['server_id']]
    save_settings()
    return jsonify({})

@app.route("/api/update_server_field", methods=['POST'])
def upd_field():
    d = request.json
    s = next((x for x in servers if x['id'] == d['server_id']), None)
    if s: 
        for k, v in d.items(): 
            if k != 'server_id': s[k] = v
        save_settings()
    return jsonify({})

@app.route("/api/toggle_all_grab", methods=['POST'])
def toggle_all():
    # Logic: Nếu có ít nhất 1 bot đang tắt -> Bật hết. Nếu đang bật hết -> Tắt hết.
    any_off = False
    for s in servers:
        for i in range(len(main_tokens)):
            if not s.get(f'auto_grab_enabled_{i+1}', False): any_off = True
    
    for s in servers:
        for i in range(len(main_tokens)):
            s[f'auto_grab_enabled_{i+1}'] = any_off
    save_settings()
    return jsonify({})

# --- MAIN ENTRY ---
if __name__ == "__main__":
    load_settings()
    
    # Khởi động Bot
    for i, t in enumerate(main_tokens):
        if t.strip():
            threading.Thread(target=run_discord_bot, args=(t.strip(), f"main_{i+1}"), daemon=True).start()
            
    # Auto Save
    threading.Thread(target=lambda: (time.sleep(1800) or save_settings()) and False, daemon=True).start()
    
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
