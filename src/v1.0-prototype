from flask import Flask, redirect, request, render_template_string
import requests
import json
import os
from datetime import datetime

app = Flask(__name__)

# --- Strava API 設定 ---
CLIENT_ID = '236024'
CLIENT_SECRET = 'f5efa92bcd5a43fa327f08c926a3bba38e91a56d'
REDIRECT_URI = 'http://localhost:5000/authorization_completed'

# --- 資料持久化路徑 ---
DATA_FILE = 'user_data.json'

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        "last_chain_date": 1704067200,
        "chain_threshold": 600,
        "last_tire_date": 1704067200,
        "tire_threshold": 5000,
        "bike_model": "Giant TCR Advanced 2"
    }

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def format_duration(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{int(h)}h {int(m)}m"

@app.route('/')
def index():
    # 首頁提供 Strava 授權連結
    strava_auth_url = f"https://www.strava.com/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=read,activity:read_all"
    return render_template_string(MENU_HTML, auth_url=strava_auth_url)

@app.route('/authorization_completed')
def auth_done():
    code = request.args.get('code')
    # 換取 Access Token
    token_response = requests.post('https://www.strava.com/oauth/token', data={
        'client_id': CLIENT_ID, 
        'client_secret': CLIENT_SECRET, 
        'code': code, 
        'grant_type': 'authorization_code'
    }).json()
    
    access_token = token_response.get('access_token')
    if not access_token:
        return "授權失敗，請檢查 API Client Secret 是否正確。"

    headers = {'Authorization': f"Bearer {access_token}"}
    activities = requests.get("https://www.strava.com/api/v3/athlete/activities", 
                              headers=headers, params={'per_page': 20}).json()

    # 處理 API 可能回傳錯誤訊息的情況
    if isinstance(activities, dict) and activities.get('errors'):
        return f"API 錯誤: {activities.get('message')}"

    db = load_data()
    
    # 里程計算邏輯
    def get_accumulated_km(since_timestamp):
        relevant = [a for a in activities if datetime.strptime(a['start_date_local'], "%Y-%m-%dT%H:%M:%SZ").timestamp() > since_timestamp]
        return round(sum(a['distance'] for a in relevant) / 1000, 2)

    chain_km = get_accumulated_km(db["last_chain_date"])
    tire_km = get_accumulated_km(db["last_tire_date"])

    # 準備圖表與日記數據
    chart_labels = [a['start_date_local'][:10] for a in reversed(activities)]
    chart_data = [round(a['distance']/1000, 2) for a in reversed(activities)]
    diary_entries = [{
        "name": a['name'], "date": a['start_date_local'][:10],
        "km": round(a['distance']/1000, 2), "gain": a.get('total_elevation_gain', 0),
        "time": format_duration(a['moving_time'])
    } for a in activities]

    return render_template_string(DASHBOARD_HTML, 
                                  db=db, chain_km=chain_km, tire_km=tire_km,
                                  labels=chart_labels, ride_data=chart_data, diary=diary_entries)

@app.route('/reset/<item>', methods=['POST'])
def reset_item(item):
    db = load_data()
    now = int(datetime.now().timestamp())
    if item == 'chain': db["last_chain_date"] = now
    elif item == 'tire': db["last_tire_date"] = now
    save_data(db)
    return redirect('/')

@app.route('/update_settings', methods=['POST'])
def update_settings():
    db = load_data()
    db["chain_threshold"] = int(request.form.get('chain_limit', 600))
    db["tire_threshold"] = int(request.form.get('tire_limit', 5000))
    save_data(db)
    return redirect('/')

# --- HTML 模板 ---

MENU_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TCR Smart Monitor</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #121212; color: white; height: 100vh; display: flex; align-items: center; justify-content: center; }
        .btn-strava { background: #fc4c02; color: white; border-radius: 50px; padding: 15px 30px; font-weight: bold; text-decoration: none; }
    </style>
</head>
<body>
    <div class="text-center">
        <h1 class="mb-4">TCR 騎行數據整合系統</h1>
        <a href="{{ auth_url }}" class="btn-strava">透過 Strava 登入同步</a>
    </div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root { --strava: #fc4c02; }
        body { background: #f8f9fa; }
        .card-custom { border-radius: 15px; border: none; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }
        .diary-card { border-left: 5px solid var(--strava); margin-bottom: 15px; }
        .progress { height: 8px; }
    </style>
</head>
<body class="py-5">
    <div class="container">
        <div class="row">
            <div class="col-md-4">
                <div class="card card-custom p-4 mb-4">
                    <h5 class="fw-bold mb-4">🔧 耗材監控</h5>
                    
                    <div class="mb-4">
                        <div class="d-flex justify-content-between small"><span>鍊條保養</span><span>{{ chain_km }}/{{ db.chain_threshold }} km</span></div>
                        <div class="progress my-2"><div class="progress-bar bg-warning" style="width:{{ (chain_km/db.chain_threshold)*100 }}%"></div></div>
                        <form action="/reset/chain" method="POST"><button class="btn btn-sm btn-outline-dark w-100">標記已上油</button></form>
                    </div>

                    <div class="mb-4">
                        <div class="d-flex justify-content-between small"><span>外胎壽命</span><span>{{ tire_km }}/{{ db.tire_threshold }} km</span></div>
                        <div class="progress my-2"><div class="progress-bar bg-danger" style="width:{{ (tire_km/db.tire_threshold)*100 }}%"></div></div>
                        <form action="/reset/tire" method="POST"><button class="btn btn-sm btn-outline-dark w-100">更換外胎</button></form>
                    </div>
                </div>
                
                <div class="card card-custom p-3 text-center">
                    <img src="https://images.giant-bicycles.com/b_white,c_pad,h_650,q_80/n8btqox1fnhpivvubfha/TCR-Advanced-2-KOM.jpg" class="img-fluid rounded mb-3">
                    <h6>{{ db.bike_model }}</h6>
                </div>
            </div>

            <div class="col-md-8">
                <div class="card card-custom p-4 mb-4">
                    <canvas id="rideChart" height="150"></canvas>
                </div>

                <h5 class="fw-bold mb-3">騎行日記</h5>
                {% for act in diary %}
                <div class="card card-custom diary-card p-3">
                    <div class="d-flex justify-content-between align-items-center">
                        <span class="fw-bold">{{ act.name }}</span>
                        <small class="text-muted">{{ act.date }}</small>
                    </div>
                    <div class="row mt-2 text-center small">
                        <div class="col-4"><b>{{ act.km }}</b> km</div>
                        <div class="col-4 border-start"><b>{{ act.gain }}</b> m</div>
                        <div class="col-4 border-start"><b>{{ act.time }}</b></div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>
    <script>
        const ctx = document.getElementById('rideChart').getContext('2d');
        new Chart(ctx, {
            type: 'line',
            data: {
                labels: {{ labels|tojson }},
                datasets: [{
                    label: '里程 (km)',
                    data: {{ ride_data|tojson }},
                    borderColor: '#fc4c02',
                    tension: 0.4,
                    fill: false
                }]
            }
        });
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, port=5000)
