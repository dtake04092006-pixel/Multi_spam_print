import discord, asyncio, threading, time, os, re, requests, json, random, traceback, uuid, base64
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv
from PIL import Image
import io

load_dotenv()

# --- CẤU HÌNH ---
# Lấy list Token và Key từ biến môi trường
main_tokens = os.getenv("MAIN_TOKENS", "").split(",")
gemini_api_keys = os.getenv("GEMINI_API_KEY", "").split(",")

# Tên model bạn yêu cầu
GEMINI_MODEL = "gemini-3-flash-preview"

karuta_id, karibbit_id = "646937666251915264", "1311684840462225440"

# --- BIẾN TRẠNG THÁI ---
servers = []
bot_states = {"active": {}, "health_stats": {}}
server_start_time = time.time()

# Chia sẻ dữ liệu
shared_drop_info = {
    "heart_data": None,
    "ocr_data": None, 
    "message_id": None,
    "timestamp": 0,
    "lock": threading.Lock()
}

class ThreadSafeBotManager:
    def __init__(self):
        self._bots = {}
        self._lock = threading.RLock()
    def add_bot(self, bot_id, bot_data):
        with self._lock: self._bots[bot_id] = bot_data
    def remove_bot(self, bot_id):
        with self._lock: return self._bots.pop(bot_id, None)
    def get_all_bots_data(self):
        with self._lock: return list(self._bots.items())

bot_manager = ThreadSafeBotManager()

# --- LƯU & TẢI CÀI ĐẶT ---
def save_settings():
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    settings_data = {'servers': servers, 'last_save_time': time.time()}
    try: requests.put(f"https://api.jsonbin.io/v3/b/{bin_id}", json=settings_data, headers={'Content-Type': 'application/json', 'X-Master-Key': api_key}, timeout=10)
    except: pass

def load_settings():
    global servers
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    try:
        req = requests.get(f"https://api.jsonbin.io/v3/b/{bin_id}/latest", headers={'X-Master-Key': api_key}, timeout=10)
        if req.status_code == 200:
            data = req.json().get("record", {})
            servers.extend(data.get('servers', []))
    except: pass

def periodic_save():
    while True:
        time.sleep(1800)
        save_settings()

# ==============================================================================
# 1. HÀM OCR DIRECT API (MODEL GEMINI 3.0)
# ==============================================================================
def scan_image_for_prints_and_edition(image_url):
    """
    Dùng requests gửi thẳng lên model gemini-3-flash-preview.
    """
    valid_keys = [k.strip() for k in gemini_api_keys if k.strip()]
    if not valid_keys:
        print("[OCR] ❌ Thiếu GEMINI_API_KEY!", flush=True)
        return []

    # print(f"[OCR] 📥 Đang tải ảnh...", flush=True)
    
    try:
        # Tải ảnh
        resp = requests.get(image_url, timeout=5)
        if resp.status_code != 200: return []
        
        img = Image.open(io.BytesIO(resp.content))
        width, height = img.size
        
        num_cards = 3 
        if width > 1000: num_cards = 4
        card_width = width // num_cards
        
        results = []
        
        # Chọn ngẫu nhiên 1 Key
        api_key = random.choice(valid_keys)
        
        # --- URL CHUẨN CHO MODEL BẠN YÊU CẦU ---
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"

        for i in range(num_cards):
            left = i * card_width
            right = (i + 1) * card_width
            # Cắt 15% dưới cùng
            crop_img = img.crop((left, int(height * 0.85), right, height))
            
            # Chuyển Base64
            buffered = io.BytesIO()
            crop_img.save(buffered, format="JPEG")
            img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

            # Payload gửi đi
            payload = {
                "contents": [{
                    "parts": [
                        {"text": "Read the Print Number and Edition Number. Output format: 'Print Edition'. Example: '1234 2'. If edition is missing, output '1234 1'."},
                        {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}
                    ]
                }]
            }
            
            try:
                ocr_resp = requests.post(api_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=5)
                
                if ocr_resp.status_code == 200:
                    data = ocr_resp.json()
                    if 'candidates' in data:
                        text = data['candidates'][0]['content']['parts'][0]['text']
                        nums = re.findall(r'\d+', text)
                        
                        p, e = 0, 1
                        if len(nums) >= 2: p, e = int(nums[0]), int(nums[1])
                        elif len(nums) == 1: p = int(nums[0])
                        
                        if p > 0:
                            # print(f"[GEMINI] ✅ Card {i+1}: #{p} · ◈{e}", flush=True)
                            results.append((i, p, e))
                else:
                    print(f"[API ERR] Code {ocr_resp.status_code}: {ocr_resp.text}", flush=True)
            except Exception as e:
                print(f"[REQ ERR] {e}", flush=True)

        return results
    except: return []

# ==============================================================================
# 2. HÀM TẠO EMBED
# ==============================================================================
async def send_yoru_embed(bot, channel_id, results):
    if not results: return
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    lines = []
    
    for idx, p, e in results:
        if idx < len(emojis):
            lines.append(f"{emojis[idx]} | **#{p} · ◈{e}**")
    
    if not lines: return

    embed = discord.Embed(description="\n".join(lines), color=0x36393f)
    embed.set_footer(text=f"Shadow AI • {GEMINI_MODEL}")

    try:
        channel = bot.get_channel(int(channel_id))
        if channel: await channel.send(embed=embed)
    except: pass

# ==============================================================================
# 3. LOGIC CHÍNH
# ==============================================================================
async def process_drop(bot, msg, bot_num):
    channel_id = msg.channel.id
    server = next((s for s in servers if str(s.get('main_channel_id')).strip() == str(channel_id)), None)
    if not server: return

    if not server.get(f'auto_grab_enabled_{bot_num}', False): return

    if bot_num == 1:
        with shared_drop_info["lock"]:
            shared_drop_info["heart_data"] = None
            shared_drop_info["ocr_data"] = None
            shared_drop_info["message_id"] = msg.id
        
        # 1. Tim
        heart_data = None
        try:
            msg = await msg.channel.fetch_message(msg.id) 
            async for m in msg.channel.history(limit=3):
                if m.author.id == int(karibbit_id) and m.created_at >= msg.created_at:
                    if m.embeds and '♡' in (m.embeds[0].description or ""):
                        lines = m.embeds[0].description.split('\n')[:4]
                        heart_data = [int(re.search(r'♡(\d+)', l).group(1)) if re.search(r'♡(\d+)', l) else 0 for l in lines]
                        break
        except: pass

        # 2. Ảnh
        ocr_data = None
        img_url = None
        if msg.embeds and msg.embeds[0].image: img_url = msg.embeds[0].image.url
        elif msg.attachments: img_url = msg.attachments[0].url
        
        if img_url:
            loop = asyncio.get_event_loop()
            ocr_data = await loop.run_in_executor(None, scan_image_for_prints_and_edition, img_url)
            if ocr_data: await send_yoru_embed(bot, channel_id, ocr_data)

        with shared_drop_info["lock"]:
            shared_drop_info["heart_data"] = heart_data
            shared_drop_info["ocr_data"] = ocr_data
            
    else:
        await asyncio.sleep(1.5)

    # --- Quyết định nhặt ---
    with shared_drop_info["lock"]:
        if shared_drop_info["message_id"] != msg.id: return
        h_data = shared_drop_info["heart_data"]
        o_data = shared_drop_info["ocr_data"]

    def get_cfg(key, default): return int(server.get(f'{key}_{bot_num}', default))
    
    mode1 = server.get(f'mode_1_active_{bot_num}', True)
    mode2 = server.get(f'mode_2_active_{bot_num}', False)
    mode3 = server.get(f'mode_3_active_{bot_num}', False)

    candidates = []

    # MODE 3
    if mode3 and h_data and o_data:
        p_dict = {i: p for i, p, e in o_data}
        valid = []
        for i, hearts in enumerate(h_data):
            if i in p_dict:
                p_val = p_dict[i]
                if (get_cfg('m3_heart_min', 50) <= hearts <= get_cfg('m3_heart_max', 99999)) and \
                   (get_cfg('m3_print_min', 1) <= p_val <= get_cfg('m3_print_max', 1000)):
                    valid.append((i, hearts, p_val))
        if valid:
            best = min(valid, key=lambda x: (x[2], -x[1]))
            candidates.append((3, best[0], 0.5, f"Mode 3 (H:{best[1]} P:{best[2]})"))

    # MODE 2
    if mode2 and o_data:
        valid = [(i, p) for i, p, e in o_data if get_cfg('print_min', 1) <= p <= get_cfg('print_max', 1000)]
        if valid:
            best = min(valid, key=lambda x: x[1])
            candidates.append((2, best[0], 0.7, f"Mode 2 (P:{best[1]})"))

    # MODE 1
    if mode1 and h_data:
        valid = [(i, h) for i, h in enumerate(h_data) if get_cfg('heart_min', 50) <= h <= get_cfg('heart_max', 99999)]
        if valid:
            best = max(valid, key=lambda x: x[1])
            candidates.append((1, best[0], 0.3, f"Mode 1 (H:{best[1]})"))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        prio, idx, delay, reason = candidates[0]
        emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"][idx]
        
        print(f"[GRAB | Bot {bot_num}] 🎯 {reason}", flush=True)
        await asyncio.sleep(delay)
        try:
            await msg.add_reaction(emoji)
            ktb = server.get('ktb_channel_id')
            if ktb: await bot.get_channel(int(ktb)).send("kt fs")
        except: pass

# --- STARTUP ---
def run_bot(token, bot_id_str, is_main):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = discord.Client(self_bot=True, heartbeat_timeout=60.0, guild_subscriptions=False)
    try: bot_num = int(bot_id_str.split('_')[1])
    except: bot_num = 1

    @bot.event
    async def on_ready(): print(f"[Login] ✅ {bot.user} ready!", flush=True)

    @bot.event
    async def on_message(msg):
        if not is_main: return
        try:
            if (msg.author.id == int(karuta_id) or msg.author.id == int(karibbit_id)) and "dropping" in msg.content.lower():
                await process_drop(bot, msg, bot_num)
        except: pass

    try:
        bot_manager.add_bot(bot_id_str, {'instance': bot, 'loop': loop})
        loop.run_until_complete(bot.start(token))
    except: pass
    finally:
        try:
            bot_manager.remove_bot(bot_id_str)
            loop.run_until_complete(bot.close())
        except: pass

# --- WEB SERVER ---
app = Flask(__name__)
HTML_TEMPLATE = """
<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shadow Control</title>
<style>body{background:#111;color:#eee;font-family:sans-serif;padding:10px}.panel{border:1px solid #444;padding:10px;margin:10px 0;background:#222}
input{background:#333;color:#fff;border:1px solid #555;padding:4px;width:100%}.btn{padding:5px 10px;cursor:pointer;background:#007bff;color:#fff;border:none}
.active{background:green}.bot-row{margin-top:5px;border-top:1px solid #333;padding-top:5px}</style></head>
<body><h2>SHADOW AI - MODEL: {{ model_name }}</h2>
<button class="btn" onclick="post('/api/add_server',{name:prompt('Name:')})">Add Server</button>
<div id="servers">
{% for s in servers %}
<div class="panel" data-id="{{s.id}}"><h3>{{s.name}} <button onclick="del('{{s.id}}')" style="background:red;float:right">X</button></h3>
ID: <input value="{{s.main_channel_id or ''}}" onchange="upd(this,'main_channel_id')">
KTB: <input value="{{s.ktb_channel_id or ''}}" onchange="upd(this,'ktb_channel_id')">
{% for b in main_bots %}
<div class="bot-row"><b>{{b.name}}</b>
<button onclick="tg(this,'1','{{b.id}}','{{s.id}}')" class="{{'active' if s['mode_1_active_'+b.id]}}">❤️</button>
<button onclick="tg(this,'2','{{b.id}}','{{s.id}}')" class="{{'active' if s['mode_2_active_'+b.id]}}">📷</button>
<button onclick="tg(this,'3','{{b.id}}','{{s.id}}')" class="{{'active' if s['mode_3_active_'+b.id]}}">⭐</button>
<button onclick="har(this,'{{b.id}}','{{s.id}}')" class="{{'active' if s['auto_grab_enabled_'+b.id]}}">RUN</button>
</div>{% endfor %}</div>{% endfor %}
</div>
<script>
const post=async(u,d)=>{await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});location.reload()};
const del=id=>confirm('Del?')&&post('/api/delete_server',{server_id:id});
const upd=(el,f)=>post('/api/update_server_field',{server_id:el.closest('.panel').dataset.id,[f]:el.value});
const tg=(el,m,bid,sid)=>post('/api/toggle_bot_mode',{server_id:sid,bot_id:bid,mode:m,active:el.classList.toggle('active')});
const har=(el,bid,sid)=>post('/api/harvest_toggle',{server_id:sid,node:bid});
</script></body></html>
"""

@app.route("/")
def index():
    main_bots = [{"id": str(i+1), "name": f"Bot {i+1}"} for i in range(len(main_tokens))]
    return render_template_string(HTML_TEMPLATE, servers=servers, main_bots=main_bots, model_name=GEMINI_MODEL)

@app.route("/api/add_server", methods=['POST'])
def add():
    name = request.json.get('name')
    if name:
        s = {"id": uuid.uuid4().hex, "name": name}
        for i in range(len(main_tokens)): s[f'mode_1_active_{i+1}'] = True
        servers.append(s); save_settings()
    return jsonify({'ok': True})

@app.route("/api/delete_server", methods=['POST'])
def delete():
    servers[:] = [s for s in servers if s['id'] != request.json.get('server_id')]
    save_settings(); return jsonify({'ok': True})

@app.route("/api/update_server_field", methods=['POST'])
def update():
    d = request.json
    s = next((x for x in servers if x['id'] == d.get('server_id')), None)
    if s:
        for k,v in d.items(): 
            if k!='server_id': s[k]=v
        save_settings()
    return jsonify({'ok': True})

@app.route("/api/toggle_bot_mode", methods=['POST'])
def toggle_mode():
    d = request.json
    s = next((x for x in servers if x['id'] == d.get('server_id')), None)
    if s: s[f'mode_{d["mode"]}_active_{d["bot_id"]}'] = d['active']; save_settings()
    return jsonify({'ok': True})

@app.route("/api/harvest_toggle", methods=['POST'])
def toggle_grab():
    d = request.json
    s = next((x for x in servers if x['id'] == d.get('server_id')), None)
    if s:
        k = f'auto_grab_enabled_{d["node"]}'
        s[k] = not s.get(k, False)
        save_settings()
    return jsonify({'ok': True})

if __name__ == "__main__":
    print(f"🚀 Bot Started - Direct Gemini Mode ({GEMINI_MODEL})", flush=True)
    load_settings()
    
    for i, token in enumerate(main_tokens):
        if token.strip():
            threading.Thread(target=run_bot, args=(token.strip(), f"main_{i+1}", True), daemon=True).start()
    
    threading.Thread(target=periodic_save, daemon=True).start()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
