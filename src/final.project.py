from flask import Flask, redirect, request, render_template_string, session, url_for
from werkzeug.utils import secure_filename
import requests
import json
import os
import uuid
from datetime import datetime
from collections import defaultdict

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'ridemonitor_pro_dual_bar_v5')

# --- 檔案上傳設定 ---
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Strava API 設定 ---
CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID', '236024')
CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET', 'f5efa92bcd5a43fa327f08c926a3bba38e91a56d')
REDIRECT_URI = 'http://localhost:5000/authorization_completed'

# --- 資料持久化路徑 ---
DATA_FILE = 'user_data.json'

def load_data():
    default_data = {
        "bike_name": "我的愛車",
        "bike_photos": [],
        "components": {}, 
        "diary_custom": {}
    }
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "components" not in data: data["components"] = default_data["components"]
                if "diary_custom" not in data: data["diary_custom"] = default_data["diary_custom"]
                if "bike_name" not in data: data["bike_name"] = default_data["bike_name"]
                if "bike_photos" not in data: data["bike_photos"] = default_data["bike_photos"]
                return data
        except:
            pass
    return default_data

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def handle_file_upload(files):
    saved_paths = []
    for file in files:
        if file and file.filename != '':
            ext = file.filename.rsplit('.', 1)[-1].lower()
            if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
                filename = f"{uuid.uuid4().hex}.{ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                saved_paths.append(f"/static/uploads/{filename}")
    return saved_paths

def fetch_strava_activities(access_token, earliest_ts):
    headers = {'Authorization': f"Bearer {access_token}"}
    activities = []
    page = 1
    per_page = 50
    while True:
        params = {'page': page, 'per_page': per_page, 'after': earliest_ts}
        try:
            resp = requests.get("https://www.strava.com/api/v3/athlete/activities", headers=headers, params=params).json()
            if not resp or not isinstance(resp, list) or (isinstance(resp, dict) and resp.get('errors')):
                break
            activities.extend(resp)
            if len(resp) < per_page: break
            page += 1
        except:
            break
    return activities

def calculate_status(activities, db):
    def get_km(since_ts):
        relevant = []
        for a in activities:
            try:
                ts = datetime.strptime(a['start_date_local'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
                if ts >= since_ts: relevant.append(a)
            except: continue
        return round(sum(a['distance'] for a in relevant) / 1000, 2)

    status = {}
    for key, comp in db["components"].items():
        # 判斷是否啟用里程限制
        has_km_limit = comp["threshold"] < 900000
        current_km = get_km(comp["last_reset"])
        km_pct = (current_km / comp["threshold"]) * 100 if (has_km_limit and comp["threshold"] > 0) else 0
        
        # 判斷是否啟用時間限制
        use_time = comp.get("use_time_enabled", False)
        days_passed = (datetime.now() - datetime.fromtimestamp(comp["last_reset"])).days
        time_pct = 0
        if use_time:
            t_limit = comp.get("time_threshold", 365)
            time_pct = (days_passed / t_limit) * 100 if t_limit > 0 else 0
            
        # OR 判斷邏輯：取里程與時間百分比的最大值作為整體卡片的危險指標
        final_pct = max(km_pct, time_pct) if (has_km_limit or use_time) else km_pct
        
        date_str = datetime.fromtimestamp(comp["last_reset"]).strftime("%Y-%m-%d")
        status[key] = {
            "name": comp.get("display_name", key), 
            "km": current_km,
            "threshold": comp["threshold"], 
            "has_km_limit": has_km_limit,
            "km_percentage": min(round(km_pct, 1), 100),
            "time_percentage": min(round(time_pct, 1), 100),
            "percentage": min(round(final_pct, 1), 100), # 決定整體卡片與通知的 OR 觸發狀態
            "start_date": date_str, 
            "can_rollback": comp["last_reset"] != comp["prev_reset"],
            "use_time_enabled": use_time,
            "time_threshold": comp.get("time_threshold", 365),
            "days_passed": days_passed
        }
    return status

def format_duration(seconds):
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"

# --- 路由控制 ---

@app.route('/')
def index():
    if 'access_token' in session: return redirect(url_for('dashboard'))
    auth_url = f"https://www.strava.com/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=read,activity:read_all"
    return render_template_string(MENU_HTML, auth_url=auth_url)

@app.route('/authorization_completed')
def auth_done():
    code = request.args.get('code')
    token_response = requests.post('https://www.strava.com/oauth/token', data={
        'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'code': code, 'grant_type': 'authorization_code'
    }).json()
    access_token = token_response.get('access_token')
    if not access_token: return "授權失敗，請確認 API 憑證。"
    session['access_token'] = access_token
    return redirect(url_for('dashboard'))

@app.route('/update_bike_profile', methods=['POST'])
def update_bike_profile():
    if 'access_token' not in session: return redirect(url_for('index'))
    db = load_data()
    db["bike_name"] = request.form.get('bike_name', '').strip() or db["bike_name"]
    
    files = request.files.getlist('bike_photos')
    new_photos = handle_file_upload(files)
    if new_photos:
        db["bike_photos"] = new_photos
        
    save_data(db)
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    token = session.get('access_token')
    if not token: return redirect(url_for('index'))
    db = load_data()
    
    activities = fetch_strava_activities(token, 1704067200)
    total_lifetime_km = round(sum(a['distance'] for a in activities) / 1000, 2)
    total_lifetime_gain = round(sum(a.get('total_elevation_gain', 0) for a in activities), 1)
    status = calculate_status(activities, db)
    
    # 計算每月累積里程
    monthly_data = defaultdict(float)
    for a in activities:
        try: 
            month_str = a['start_date_local'][:7]
            monthly_data[month_str] += (a['distance'] / 1000)
        except: continue
            
    # 固定連續推算過去 12 個月，無數據補 0
    today = datetime.now()
    chart_labels = []
    for i in range(11, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        chart_labels.append(f"{y}-{m:02d}")
        
    chart_data = [round(monthly_data.get(m, 0.0), 2) for m in chart_labels]
    
    return render_template_string(DASHBOARD_HTML, db=db, status=status, labels=chart_labels, 
                                  ride_data=chart_data, total_km=total_lifetime_km, 
                                  total_gain=total_lifetime_gain, active_page='dashboard', nav_bar=NAV_BAR)

@app.route('/maintenance')
def maintenance():
    token = session.get('access_token')
    if not token: return redirect(url_for('index'))
    db = load_data()
    activities = fetch_strava_activities(token, 1704067200)
    status = calculate_status(activities, db)
    return render_template_string(MAINTENANCE_HTML, db=db, status=status, active_page='maintenance', nav_bar=NAV_BAR)

@app.route('/diary')
def diary():
    token = session.get('access_token')
    if not token: return redirect(url_for('index'))
    db = load_data()
    activities = fetch_strava_activities(token, 1704067200)
    
    view_mode = request.args.get('view', 'feed')
    diary_entries = []
    
    for a in activities:
        act_id = str(a['id'])
        custom = db["diary_custom"].get(act_id, {"notes": "", "photos": [], "is_hidden": False})
        is_hidden = custom.get("is_hidden", False)
        
        if view_mode == 'feed' and is_hidden: continue
        if view_mode == 'hidden' and not is_hidden: continue
        
        dist_km = round(a['distance']/1000, 2)
        elev_gain = a.get('total_elevation_gain', 0)
        
        if dist_km >= 100 or elev_gain >= 1000:
            intensity_color = "#dc3545"
            intensity_badge = "🔥 高強度"
        elif dist_km >= 50 or elev_gain >= 500:
            intensity_color = "#fd7e14"
            intensity_badge = "⚡ 中強度"
        else:
            intensity_color = "#198754"
            intensity_badge = "🌱 休閒騎"
            
        photos = custom.get("photos", [])
        diary_entries.append({
            "id": act_id, "name": a['name'], "date": a['start_date_local'][:10],
            "km": dist_km, "gain": elev_gain, "time": format_duration(a['moving_time']),
            "notes": custom.get("notes", ""), "photos": photos,
            "is_hidden": is_hidden, "intensity_color": intensity_color, "intensity_badge": intensity_badge
        })
    return render_template_string(DIARY_HTML, diary=diary_entries, view_mode=view_mode, active_page='diary', nav_bar=NAV_BAR)

@app.route('/diary/edit/<act_id>', methods=['GET', 'POST'])
def edit_diary(act_id):
    if 'access_token' not in session: return redirect(url_for('index'))
    db = load_data()
    act_id_str = str(act_id)
    
    if act_id_str not in db["diary_custom"]:
        db["diary_custom"][act_id_str] = {"notes": "", "photos": [], "is_hidden": False}
            
    if request.method == 'POST':
        db["diary_custom"][act_id_str]["notes"] = request.form.get('notes', '')
        files = request.files.getlist('diary_photos')
        new_photos = handle_file_upload(files)
        
        if request.form.get('clear_photos') == 'yes':
            db["diary_custom"][act_id_str]["photos"] = []
        if new_photos:
            db["diary_custom"][act_id_str]["photos"].extend(new_photos)
            
        save_data(db)
        return redirect(url_for('diary'))
        
    custom = db["diary_custom"].get(act_id_str, {"notes": "", "photos": []})
    act_name = request.args.get('name', '騎行活動')
    return render_template_string(EDIT_DIARY_HTML, act_id=act_id, act_name=act_name, custom=custom)

@app.route('/diary/toggle_hide/<act_id>', methods=['POST'])
def toggle_hide(act_id):
    db = load_data()
    act_id_str = str(act_id)
    if act_id_str not in db["diary_custom"]:
        db["diary_custom"][act_id_str] = {"notes": "", "photos": [], "is_hidden": False}
    db["diary_custom"][act_id_str]["is_hidden"] = not db["diary_custom"][act_id_str].get("is_hidden", False)
    save_data(db)
    return redirect(request.referrer or url_for('diary'))

@app.route('/set_start_date/<item>', methods=['POST'])
def set_start_date(item):
    db = load_data()
    if item in db["components"]:
        date_str = request.form.get('start_date')
        if date_str:
            try:
                ts = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
                db["components"][item]["prev_reset"] = db["components"][item]["last_reset"]
                db["components"][item]["last_reset"] = ts
                save_data(db)
            except: pass
    return redirect(url_for('maintenance'))

@app.route('/reset/<item>', methods=['POST'])
def reset_item(item):
    db = load_data()
    if item in db["components"]:
        db["components"][item]["prev_reset"] = db["components"][item]["last_reset"]
        db["components"][item]["last_reset"] = int(datetime.now().timestamp())
        save_data(db)
    return redirect(url_for('maintenance'))

@app.route('/rollback/<item>', methods=['POST'])
def rollback_item(item):
    db = load_data()
    if item in db["components"]:
        db["components"][item]["last_reset"] = db["components"][item]["prev_reset"]
        save_data(db)
    return redirect(url_for('maintenance'))

@app.route('/add_component', methods=['POST'])
def add_component():
    db = load_data()
    name = request.form.get('comp_name', '').strip()
    limit = request.form.get('comp_limit', '').strip()
    time_enabled = 'time_enabled' in request.form
    time_limit = request.form.get('time_limit', '365')
    
    if name:
        key = f"custom_{int(datetime.now().timestamp())}"
        db["components"][key] = {
            "display_name": name, 
            "last_reset": int(datetime.now().timestamp()),
            "prev_reset": int(datetime.now().timestamp()), 
            "threshold": int(limit) if (limit.isdigit() and int(limit) > 0) else 999999,
            "use_time_enabled": time_enabled,
            "time_threshold": int(time_limit) if time_limit.isdigit() else 365
        }
        save_data(db)
    return redirect(url_for('maintenance'))

@app.route('/delete_component/<item>', methods=['POST'])
def delete_component(item):
    db = load_data()
    if item in db["components"]:
        del db["components"][item]
        save_data(db)
    return redirect(url_for('maintenance'))

@app.route('/update_settings', methods=['POST'])
def update_settings():
    db = load_data()
    for key in db["components"].keys():
        val = request.form.get(f'{key}_limit')
        if val: db["components"][key]["threshold"] = int(val)
        
        time_val = request.form.get(f'{key}_time_limit')
        if time_val: db["components"][key]["time_threshold"] = int(time_val)
        
        db["components"][key]["use_time_enabled"] = f'{key}_time_enabled' in request.form
    save_data(db)
    return redirect(url_for('maintenance'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# ==========================================
#               UI 模板區塊
# ==========================================

NAV_BAR = """
<nav class="navbar navbar-expand-lg navbar-dark bg-dark mb-4 shadow-sm">
    <div class="container">
        <a class="navbar-brand fw-bold text-warning" href="#">🚲 RideMonitor Pro</a>
        <div class="navbar-nav me-auto">
            <a class="nav-link {% if active_page=='dashboard' %}active fw-bold{% endif %}" href="/dashboard">儀表板總覽</a>
            <a class="nav-link {% if active_page=='maintenance' %}active fw-bold{% endif %}" href="/maintenance">耗材保養系統</a>
            <a class="nav-link {% if active_page=='diary' %}active fw-bold{% endif %}" href="/diary">騎行日記 Feed</a>
        </div>
        <a href="/logout" class="btn btn-sm btn-outline-light">登出</a>
    </div>
</nav>
"""

MENU_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>RideMonitor 騎行整合系統</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body { background: #121212; color: white; height: 100vh; display: flex; align-items: center; justify-content: center; }</style>
</head>
<body>
    <div class="text-center">
        <h1 class="mb-4 fw-bold">RideMonitor 騎行數據整合系統</h1>
        <a href="{{ auth_url }}" class="btn btn-lg btn-danger px-4 py-2" style="background:#fc4c02; border:none; border-radius:30px;">透過 Strava 數據同步登入</a>
    </div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>儀表板總覽 - RideMonitor Pro</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</head>
<body class="bg-light">
    {{ nav_bar|safe }}
    <div class="container">
        <div class="row mb-4">
            <div class="col-md-6 mb-2">
                <div class="card border-0 shadow-sm text-white" style="background: linear-gradient(135deg, #fc4c02, #ff7b00); border-radius:15px;">
                    <div class="card-body p-4">
                        <h6 class="text-white-50 text-uppercase fw-bold mb-1">平台總累積騎乘里程 (Lifetime)</h6>
                        <h2 class="fw-extrabold m-0" style="font-size: 2.5rem;">{{ total_km }} <span style="font-size: 1.2rem;">km</span></h2>
                    </div>
                </div>
            </div>
            <div class="col-md-6 mb-2">
                <div class="card border-0 shadow-sm text-white" style="background: linear-gradient(135deg, #1f2d3d, #34495e); border-radius:15px;">
                    <div class="card-body p-4">
                        <h6 class="text-white-50 text-uppercase fw-bold mb-1">平台總累積爬升高度 (Elevation)</h6>
                        <h2 class="fw-extrabold m-0" style="font-size: 2.5rem;">{{ total_gain }} <span style="font-size: 1.2rem;">m</span></h2>
                    </div>
                </div>
            </div>
        </div>

        <div class="row">
            <div class="col-md-4 mb-4">
                <div class="card border-0 shadow-sm p-4 text-center mb-4" style="border-radius:15px;">
                    {% if db.bike_photos|length > 0 %}
                    <div id="bikeCarousel" class="carousel slide mb-3 shadow-sm rounded" data-bs-ride="carousel">
                        <div class="carousel-inner rounded" style="max-height: 250px;">
                            {% for photo in db.bike_photos %}
                            <div class="carousel-item {% if loop.index == 1 %}active{% endif %}">
                                <img src="{{ photo }}" class="d-block w-100" style="height: 250px; object-fit: cover;">
                            </div>
                            {% endfor %}
                        </div>
                        {% if db.bike_photos|length > 1 %}
                        <button class="carousel-control-prev" type="button" data-bs-target="#bikeCarousel" data-bs-slide="prev">
                            <span class="carousel-control-prev-icon" aria-hidden="true"></span>
                        </button>
                        <button class="carousel-control-next" type="button" data-bs-target="#bikeCarousel" data-bs-slide="next">
                            <span class="carousel-control-next-icon" aria-hidden="true"></span>
                        </button>
                        {% endif %}
                    </div>
                    {% else %}
                    <div class="bg-secondary rounded mb-3 d-flex align-items-center justify-content-center text-white" style="height: 250px;">尚無單車照片</div>
                    {% endif %}
                    
                    <h5 class="fw-bold m-0">{{ db.bike_name }}</h5>
                    <button class="btn btn-sm btn-outline-secondary mt-3" type="button" data-bs-toggle="collapse" data-bs-target="#editProfileForm">⚙️ 編輯愛車資料</button>
                    
                    <div class="collapse mt-3 text-start" id="editProfileForm">
                        <div class="card card-body bg-light border-0">
                            <form action="/update_bike_profile" method="POST" enctype="multipart/form-data">
                                <div class="mb-2">
                                    <label class="form-label small fw-bold">單車名稱</label>
                                    <input type="text" class="form-control form-control-sm" name="bike_name" value="{{ db.bike_name }}">
                                </div>
                                <div class="mb-3">
                                    <label class="form-label small fw-bold">本機上傳新照片 (可多選)</label>
                                    <input type="file" class="form-control form-control-sm" name="bike_photos" multiple accept="image/*">
                                </div>
                                <button type="submit" class="btn btn-primary btn-sm w-100">儲存變更</button>
                            </form>
                        </div>
                    </div>
                </div>
                
                <div class="card border-0 shadow-sm p-4" style="border-radius:15px;">
                    <h5 class="fw-bold mb-3 text-secondary">🚨 狀態緊繃耗材</h5>
                    {% set alerts = namespace(count=0) %}
                    {% for key, comp in status.items() %}
                        {% if comp.percentage >= 85 %}
                        <div class="alert alert-danger py-2 mb-2 small d-flex justify-content-between align-items-center">
                            <span>⚠️ <b>{{ comp.name }}</b> 已達臨界指標！</span>
                            <a href="/maintenance" class="btn btn-xs btn-danger p-1 text-white" style="font-size:10px;">去檢查</a>
                        </div>
                        {% set alerts.count = alerts.count + 1 %}
                        {% endif %}
                    {% endfor %}
                    {% if alerts.count == 0 %}
                        <p class="text-success m-0 small">✓ 目前所有零件與週期保養皆在安全壽命內！</p>
                    {% endif %}
                </div>
            </div>
            
            <div class="col-md-8">
                <div class="card border-0 shadow-sm p-4 mb-4" style="border-radius:15px;">
                    <h5 class="fw-bold mb-3 text-dark">📊 過去一年累積騎行里程</h5>
                    <div style="position: relative; height: 300px; width: 100%;">
                        <canvas id="rideChart"></canvas>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        const ctx = document.getElementById('rideChart').getContext('2d');
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: {{ labels|tojson }},
                datasets: [{
                    label: '里程 (km)',
                    data: {{ ride_data|tojson }},
                    backgroundColor: '#fc4c02',
                    borderRadius: 4
                }]
            },
            options: { 
                maintainAspectRatio: false,
                plugins: { legend: { display: false } }, 
                scales: { y: { beginAtZero: true } } 
            }
        });
    </script>
</body>
</html>
"""

MAINTENANCE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>耗材保養管理 - RideMonitor Pro</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    {{ nav_bar|safe }}
    <div class="container">
        <div class="row">
            <div class="col-md-8 mb-4">
                <div class="card border-0 shadow-sm p-4" style="border-radius:15px;">
                    <h4 class="fw-bold mb-4">🔧 當前耗材狀態與雙軌獨立進度條</h4>
                    {% if not status %}
                        <div class="text-center py-5 text-muted"><p>目前沒有追蹤任何耗材，請從右側面板新增。</p></div>
                    {% endif %}
                    {% for key, comp in status.items() %}
                    <div class="mb-4 p-3 bg-white rounded border-start border-4 {% if comp.percentage >= 85 %}border-danger{% else %}border-warning{% endif %} shadow-sm">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <div>
                                <span class="fw-bold text-dark fs-5">{{ comp.name }}</span>
                            </div>
                            <span class="badge {% if comp.percentage >= 85 %}bg-danger{% else %}bg-warning{% endif %} text-white">
                                Warning：{{ comp.percentage }}%
                            </span>
                        </div>
                        
                        {% if comp.has_km_limit %}
                        <div class="mb-3">
                            <div class="d-flex justify-content-between small text-muted mb-1">
                                <span>🛣️ <b>里程:</b> {{ comp.km }} / {{ comp.threshold }} km</span>
                                <span class="fw-bold">{{ comp.km_percentage }}%</span>
                            </div>
                            <div class="progress" style="height:8px;">
                                <div class="progress-bar {% if comp.km_percentage >= 85 %}bg-danger{% else %}bg-warning{% endif %}" style="width: {{ comp.km_percentage }}%"></div>
                            </div>
                        </div>
                        {% endif %}
                        
                        {% if comp.use_time_enabled %}
                        <div class="mb-3">
                            <div class="d-flex justify-content-between small text-muted mb-1">
                                <span>📅 <b>時間:</b> 已過 {{ comp.days_passed }} 天 / 週期 {{ comp.time_threshold }} 天</span>
                                <span class="fw-bold">{{ comp.time_percentage }}%</span>
                            </div>
                            <div class="progress" style="height:8px;">
                                <div class="progress-bar {% if comp.time_percentage >= 85 %}bg-danger{% else %}bg-warning{% endif %}" style="width: {{ comp.time_percentage }}%"></div>
                            </div>
                        </div>
                        {% endif %}

                        <div class="d-flex justify-content-between align-items-center mt-3 bg-light p-2 rounded">
                            <form action="/set_start_date/{{ key }}" method="POST" class="d-flex align-items-center gap-2">
                                <label class="small text-muted mb-0 fw-bold text-nowrap">📅 重設基準日：</label>
                                <input type="date" class="form-control form-control-sm" name="start_date" value="{{ comp.start_date }}" style="max-width: 140px;">
                                <button type="submit" class="btn btn-xs btn-outline-primary py-0 px-2" style="font-size: 12px;">調整</button>
                            </form>
                            <div class="d-flex gap-2">
                                {% if comp.can_rollback %}
                                <form action="/rollback/{{ key }}" method="POST"><button class="btn btn-sm btn-outline-secondary py-1">↩ 復原重置</button></form>
                                {% endif %}
                                <form action="/reset/{{ key }}" method="POST"><button class="btn btn-sm btn-dark py-1">➔ 標記保養更換</button></form>
                                <form action="/delete_component/{{ key }}" method="POST"><button class="btn btn-sm btn-outline-danger py-1">🗑 刪除</button></form>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            
            <div class="col-md-4">
                <div class="card border-0 shadow-sm p-4 mb-4" style="border-radius:15px;">
                    <h5 class="fw-bold mb-3">➕ 新增自定義監控項目</h5>
                    <form action="/add_component" method="POST">
                        <div class="mb-3">
                            <label class="form-label small fw-bold">項目名稱</label>
                            <input type="text" class="form-control" name="comp_name" placeholder="例如：車手把帶、全車大保養" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label small fw-bold">安全里程上限 (km)</label>
                            <input type="number" class="form-control" name="comp_limit" placeholder="純時間控制項目請留空">
                        </div>
                        <div class="mb-3 bg-light p-2 rounded">
                            <div class="form-check form-switch">
                                <input class="form-check-input" type="checkbox" role="switch" id="time_enabled" name="time_enabled" checked>
                                <label class="form-check-label small fw-bold" for="time_enabled">啟用時間週期判定</label>
                            </div>
                            <div class="mt-2">
                                <label class="form-label small">安全時間上限 (天數)</label>
                                <input type="number" class="form-control form-control-sm" name="time_limit" value="365" placeholder="例如：把帶 365 天">
                            </div>
                        </div>
                        <button type="submit" class="btn btn-sm btn-success w-100">建立追蹤項目</button>
                    </form>
                </div>
                
                {% if status %}
                <div class="card border-0 shadow-sm p-4" style="border-radius:15px;">
                    <h5 class="fw-bold mb-3">⚙ 批量修改現有閥值</h5>
                    <form action="/update_settings" method="POST">
                        {% for key, comp in status.items() %}
                        <div class="mb-3 border-bottom pb-2">
                            <label class="form-label small fw-bold text-dark mb-1">{{ comp.name }}</label>
                            <div class="input-group input-group-sm mb-1">
                                <span class="input-group-text">里程</span>
                                <input type="number" class="form-control" name="{{ key }}_limit" value="{{ comp.threshold }}">
                                <span class="input-group-text">km</span>
                            </div>
                            <div class="form-check form-switch my-1">
                                <input class="form-check-input" type="checkbox" name="{{ key }}_time_enabled" id="time_en_{{ key }}" {% if comp.use_time_enabled %}checked{% endif %}>
                                <label class="form-check-label small" for="time_en_{{ key }}">時間控制</label>
                            </div>
                            <div class="input-group input-group-sm">
                                <span class="input-group-text">天數</span>
                                <input type="number" class="form-control" name="{{ key }}_time_limit" value="{{ comp.time_threshold }}">
                                <span class="input-group-text">天</span>
                            </div>
                        </div>
                        {% endfor %}
                        <button type="submit" class="btn btn-sm btn-primary w-100 mt-2">儲存批量更新</button>
                    </form>
                </div>
                {% endif %}
            </div>
        </div>
    </div>
</body>
</html>
"""

DIARY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>騎行日記 - RideMonitor Pro</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</head>
<body class="bg-light">
    {{ nav_bar|safe }}
    <div class="container" style="max-width: 800px;">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h4 class="fw-bold m-0">📝 數據騎行日記</h4>
            <div class="btn-group" role="group">
                <a href="/diary?view=feed" class="btn btn-sm {% if view_mode == 'feed' %}btn-primary fw-bold{% else %}btn-outline-primary{% endif %}">全部 Feed</a>
                <a href="/diary?view=hidden" class="btn btn-sm {% if view_mode == 'hidden' %}btn-warning fw-bold{% else %}btn-outline-warning{% endif %}">隱藏項目</a>
            </div>
        </div>

        {% if not diary %}
        <div class="text-center my-5 py-5 bg-white rounded shadow-sm">
            <p class="text-muted m-0">此區域目前沒有任何騎行活動。</p>
        </div>
        {% endif %}

        {% for act in diary %}
        <div class="card border-0 shadow-sm p-4 mb-4" style="border-radius:15px; border-left: 6px solid {% if act.is_hidden %}#6c757d{% else %}{{ act.intensity_color }}{% endif %} !important;">
            <div class="d-flex justify-content-between align-items-start border-bottom pb-2">
                <div>
                    <h5 class="fw-bold text-dark m-0 d-flex align-items-center gap-2">
                        {{ act.name }}
                        {% if not act.is_hidden %}
                        <span class="badge" style="background-color: {{ act.intensity_color }}; font-size: 12px;">{{ act.intensity_badge }}</span>
                        {% endif %}
                    </h5>
                    <small class="text-muted">{{ act.date }}</small>
                </div>
                <div class="d-flex gap-2">
                    <form action="/diary/toggle_hide/{{ act.id }}" method="POST">
                        <button type="submit" class="btn btn-sm {% if act.is_hidden %}btn-outline-success{% else %}btn-outline-secondary{% endif %} py-1">
                            {% if act.is_hidden %}👁️ 取消隱藏{% else %}👁️‍局 標記隱藏{% endif %}
                        </button>
                    </form>
                    <a href="/diary/edit/{{ act.id }}?name={{ act.name }}&date={{ act.date }}" class="btn btn-sm btn-outline-primary py-1">✏ 編輯筆記</a>
                </div>
            </div>
            
            <div class="row text-center my-3 bg-light py-2 rounded g-0">
                <div class="col-4">里程 <br><b class="text-dark fs-5">{{ act.km }}</b> km</div>
                <div class="col-4 border-start">爬升 <br><b class="text-dark fs-5">{{ act.gain }}</b> m</div>
                <div class="col-4 border-start">時間 <br><b class="text-dark fs-5">{{ act.time }}</b></div>
            </div>

            {% if act.notes %}
            <div class="p-2 bg-white rounded border mb-2 text-secondary" style="font-size: 14px; white-space: pre-line;">
                {{ act.notes }}
            </div>
            {% endif %}

            {% if act.photos|length > 0 %}
            <div class="mt-3 text-center">
                <div id="carousel-{{ act.id }}" class="carousel slide shadow-sm rounded" data-bs-ride="carousel">
                    <div class="carousel-inner rounded" style="max-height: 400px;">
                        {% for photo in act.photos %}
                        <div class="carousel-item {% if loop.index == 1 %}active{% endif %}">
                            <img src="{{ photo }}" class="d-block w-100" style="object-fit: contain; max-height: 400px; background: #000;">
                        </div>
                        {% endfor %}
                    </div>
                    {% if act.photos|length > 1 %}
                    <button class="carousel-control-prev" type="button" data-bs-target="#carousel-{{ act.id }}" data-bs-slide="prev">
                        <span class="carousel-control-prev-icon" aria-hidden="true"></span>
                    </button>
                    <button class="carousel-control-next" type="button" data-bs-target="#carousel-{{ act.id }}" data-bs-slide="next">
                        <span class="carousel-control-next-icon" aria-hidden="true"></span>
                    </button>
                    {% endif %}
                </div>
            </div>
            {% endif %}
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

EDIT_DIARY_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>編輯日記 - RideMonitor Pro</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light py-5">
    <div class="container" style="max-width: 600px;">
        <div class="card border-0 shadow p-4" style="border-radius:15px;">
            <h4 class="fw-bold mb-2">✏ 編輯騎行心得</h4>
            <p class="text-muted small mb-4">{{ act_name }}</p>
            
            <form action="/diary/edit/{{ act_id }}" method="POST" enctype="multipart/form-data">
                <div class="mb-3">
                    <label class="form-label fw-bold small">文字紀錄 / 心得筆記</label>
                    <textarea class="form-control" name="notes" rows="5" placeholder="今天狀態不錯..." style="border-radius:10px;">{{ custom.notes }}</textarea>
                </div>
                
                <div class="mb-3">
                    <label class="form-label fw-bold small">上傳本機相片 (支援多選)</label>
                    <input type="file" class="form-control" name="diary_photos" multiple accept="image/*" style="border-radius:10px;">
                </div>
                
                {% if custom.photos|length > 0 %}
                <div class="mb-4 p-3 bg-light rounded">
                    <label class="form-label fw-bold small d-block">目前已保存 {{ custom.photos|length }} 張相片</label>
                    <div class="form-check form-switch mt-2">
                        <input class="form-check-input" type="checkbox" role="switch" id="clearPhotos" name="clear_photos" value="yes">
                        <label class="form-check-label text-danger small" for="clearPhotos">清空並刪除現有相片 (勾選後儲存即生效)</label>
                    </div>
                </div>
                {% endif %}
                
                <div class="d-flex gap-2">
                    <a href="/diary" class="btn btn-outline-secondary w-50" style="border-radius:10px;">取消</a>
                    <button type="submit" class="btn btn-primary w-50" style="border-radius:10px;">儲存日記</button>
                </div>
            </form>
        </div>
    </div>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, port=5000)
