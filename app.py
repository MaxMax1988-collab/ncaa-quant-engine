import streamlit as st
import pandas as pd
import numpy as np
import requests
import psycopg2
from datetime import datetime, timedelta, timezone
from rapidfuzz import process, fuzz
from scipy.stats import norm

# --- KONFIGURACJA STRONY ---
st.set_page_config(
    page_title="Maksbet NCAA - Master Canonical Quant Engine v5.3",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLE CSS ---
st.markdown("""
    <style>
    .main { background-color: #0b0f19; color: #f3f4f6; }
    [data-testid="stSidebar"] { background-color: #111827; border-right: 1px solid #1f2937; }
    .stMetric { background: linear-gradient(135deg, #1f2937 0%, #111827 100%); padding: 15px; border-radius: 14px; border: 1px solid #374151; }
    .value-card { background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border-left: 6px solid #3b82f6; padding: 20px; border-radius: 12px; margin-bottom: 15px; border: 1px solid #334155; }
    .test-box { background: #111827; border: 1px solid #374151; padding: 15px; border-radius: 10px; margin-bottom: 10px; font-family: monospace; font-size: 13px; }
    .badge-power { background-color: #059669; color: #ffffff; padding: 3px 8px; border-radius: 6px; font-size: 11px; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- CONFIG API (NOWY KLUCZ) ---
ODDS_API_KEY = "fb92c775-169b-4df2-811a-7932f0355639"
ODDS_BASE_URL = "https://api.the-odds-api.com/v4/sports"

CFBD_TEAMS_DATABASE = [
    "Air Force", "Akron", "Alabama", "Appalachian State", "Arizona", "Arizona State", "Arkansas", "Arkansas State",
    "Army", "Auburn", "Ball State", "Baylor", "Boise State", "Boston College", "Bowling Green", "Buffalo",
    "BYU", "California", "Central Michigan", "Charlotte", "Cincinnati", "Clemson", "Coastal Carolina", "Colorado",
    "Colorado State", "Duke", "East Carolina", "Eastern Michigan", "FIU", "Florida", "Florida Atlantic", "Florida State",
    "Fresno State", "Georgia", "Georgia Southern", "Georgia State", "Georgia Tech", "Hawaii", "Houston", "Illinois",
    "Indiana", "Iowa", "Iowa State", "James Madison", "Kansas", "Kansas State", "Kennesaw State", "Kent State", "Kentucky",
    "Liberty", "Louisiana", "Louisiana Tech", "Louisville", "LSU", "Marshall", "Maryland", "Memphis",
    "Miami", "Miami (OH)", "Michigan", "Michigan State", "Middle Tennessee", "Minnesota", "Mississippi State", "Missouri", "Missouri State",
    "Navy", "NC State", "Nebraska", "Nevada", "New Mexico", "New Mexico State", "North Carolina", "North Texas",
    "Northern Illinois", "Northwestern", "Notre Dame", "Ohio", "Ohio State", "Oklahoma", "Oklahoma State", "Old Dominion",
    "Ole Miss", "Oregon", "Oregon State", "Penn State", "Pittsburgh", "Purdue", "Rice", "Rutgers",
    "Sam Houston State", "San Diego State", "San Jose State", "SMU", "South Alabama", "South Carolina", "South Florida", "Southern Miss",
    "Stanford", "Syracuse", "TCU", "Temple", "Tennessee", "Texas", "Texas A&M", "Texas State",
    "Texas Tech", "Toledo", "Troy", "Tulane", "Tulsa", "UAB", "UCF", "UCLA",
    "UConn", "UMass", "UNLV", "USC", "Utah", "Utah State", "UTEP", "UTSA",
    "Vanderbilt", "Virginia", "Virginia Tech", "Wake Forest", "Washington", "Washington State", "West Virginia", "Western Kentucky",
    "Western Michigan", "Wisconsin", "Wyoming"
]

MASTER_CANONICAL_MAP = {
    "FIU Panthers": "FIU", "Florida International": "FIU",
    "Marshall Thundering Herd": "Marshall", "Colorado Buffaloes": "Colorado",
    "Utah Utes": "Utah", "Oregon Ducks": "Oregon", "Nebraska Cornhuskers": "Nebraska",
    "UCLA Bruins": "UCLA", "Wisconsin Badgers": "Wisconsin", "Oklahoma Sooners": "Oklahoma",
    "Kentucky Wildcats": "Kentucky", "Stanford Cardinal": "Stanford", "Houston Cougars": "Houston",
    "Notre Dame Fighting Irish": "Notre Dame", "Syracuse Orange": "Syracuse", "Louisville Cardinals": "Louisville",
    "Florida State Seminoles": "Florida State", "Baylor Bears": "Baylor", "TCU Horned Frogs": "TCU",
    "Oregon State Beavers": "Oregon State", "Washington State Cougars": "Washington State",
    "Vanderbilt Commodores": "Vanderbilt", "Arkansas Razorbacks": "Arkansas", "Alabama Crimson Tide": "Alabama",
    "Missouri Tigers": "Missouri", "Duke Blue Devils": "Duke", "Florida Gators": "Florida"
}

def normalize_team_name(team_name_raw):
    if not team_name_raw:
        return "Unknown"
    clean_raw = team_name_raw.strip()
    if clean_raw in MASTER_CANONICAL_MAP:
        return MASTER_CANONICAL_MAP[clean_raw]
    for team in CFBD_TEAMS_DATABASE:
        if team.lower() == clean_raw.lower() or team.lower() in clean_raw.lower():
            return team
    return clean_raw

# --- POŁĄCZENIE Z BAZĄ NEON POSTGRESQL ---
@st.cache_resource
def init_cloud_db():
    try:
        conn_str = st.secrets["postgres"]["connection_string"]
        conn = psycopg2.connect(conn_str)
        return conn
    except Exception as e:
        return None

db_conn = init_cloud_db()

def setup_database_schema():
    if not db_conn:
        return
    cursor = db_conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ncaa_bets (
            id VARCHAR(50) PRIMARY KEY,
            date VARCHAR(20),
            match_name TEXT,
            odds REAL,
            ai_prob REAL,
            edge REAL,
            stake REAL,
            status VARCHAR(20),
            profit REAL,
            home_team VARCHAR(100),
            away_team VARCHAR(100),
            best_pick VARCHAR(100)
        );
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS team_power_ratings (
            team_name VARCHAR(100) PRIMARY KEY,
            rating REAL
        );
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS model_weights (
            param_key VARCHAR(50) PRIMARY KEY,
            param_value REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    cursor.execute("INSERT INTO model_weights (param_key, param_value) VALUES ('blend_weight', 0.15) ON CONFLICT (param_key) DO NOTHING;")
    cursor.execute("INSERT INTO model_weights (param_key, param_value) VALUES ('home_field_advantage', 2.5) ON CONFLICT (param_key) DO NOTHING;")
    
    for team in CFBD_TEAMS_DATABASE:
        cursor.execute('INSERT INTO team_power_ratings (team_name, rating) VALUES (%s, %s) ON CONFLICT (team_name) DO NOTHING;', (team, 10.0))
    db_conn.commit()
    cursor.close()

if db_conn:
    setup_database_schema()

def get_power_rating(team_name):
    if not db_conn:
        return 10.0
    cursor = db_conn.cursor()
    cursor.execute("SELECT rating FROM team_power_ratings WHERE team_name = %s", (team_name,))
    row = cursor.fetchone()
    cursor.close()
    return row[0] if row else 10.0

# --- PANEL BOCZNY ---
with st.sidebar:
    st.markdown("### 🏈 Maksbet NCAA Quant")
    st.caption("Master Canonical Engine v5.3 (Pure API)")
    st.markdown("---")
    bankroll = st.number_input("Kapitał początkowy (PLN)", min_value=100, value=1000, step=100)
    kelly_fraction = st.selectbox("Strategia Kellego", [0.1, 0.25, 0.5], index=1, format_func=lambda x: f"{int(x*100)}% Kellego")
    min_edge_slider = st.slider("Minimalny Edge (%) do akceptacji", min_value=0.0, max_value=10.0, value=0.5, step=0.1) / 100.0

# --- POBIERANIE CZYSTYCH DANYCH Z API ---
@st.cache_data(ttl=300, show_spinner=False)
def fetch_ncaa_data():
    url = f"{ODDS_BASE_URL}/americanfootball_ncaaf/odds"
    params = {"apiKey": ODDS_API_KEY, "regions": "us,eu", "markets": "h2h", "oddsFormat": "decimal"}
    all_matches = []
    api_status_code = None
    raw_response_data = []
    
    try:
        res = requests.get(url, params=params, timeout=10)
        api_status_code = res.status_code
        if res.status_code == 200:
            raw_response_data = res.json()
            for match in raw_response_data:
                match_id = str(match.get('id'))
                home = normalize_team_name(match.get('home_team'))
                away = normalize_team_name(match.get('away_team'))
                
                h2h_home, h2h_away = [], []
                for book in match.get('bookmakers', []):
                    for mkt in book.get('markets', []):
                        if mkt.get('key') == 'h2h':
                            for out in mkt.get('outcomes', []):
                                p = float(out.get('price', 0))
                                name = normalize_team_name(out.get('name'))
                                if name == home: h2h_home.append(p)
                                elif name == away: h2h_away.append(p)
                
                if h2h_home and h2h_away:
                    all_matches.append({
                        "id": match_id, "home": home, "away": away,
                        "commence_time": match.get('commence_time', 'Brak daty'),
                        "odds_home": float(np.median(h2h_home)),
                        "odds_away": float(np.median(h2h_away))
                    })
    except Exception as e:
        api_status_code = str(e)
        
    return all_matches, api_status_code, len(raw_response_data)

raw_matches, api_status, raw_count = fetch_ncaa_data()
analyzed_matches = []

cursor = db_conn.cursor() if db_conn else None

for m in raw_matches:
    match_id = m['id']
    match_name = f"{m['home']} vs {m['away']}"
    odds_home, odds_away = m['odds_home'], m['odds_away']
    
    prob_raw_h, prob_raw_a = 1.0 / odds_home, 1.0 / odds_away
    tot_v = prob_raw_h + prob_raw_a
    implied_h, implied_a = prob_raw_h / tot_v, prob_raw_a / tot_v
    
    rat_h, rat_a = get_power_rating(m['home']), get_power_rating(m['away'])
    expected_margin = (rat_h - rat_a) + 2.5
    model_prob_h = norm.cdf(expected_margin, loc=0, scale=13.5)
    
    ai_prob_home = np.clip(0.85 * implied_h + 0.15 * model_prob_h, 0.05, 0.95)
    ai_prob_away = 1.0 - ai_prob_home
    
    edge_home = (ai_prob_home * odds_home) - 1.0
    edge_away = (ai_prob_away * odds_away) - 1.0
    
    if edge_home >= edge_away:
        best_pick, best_prob, best_odds, best_edge = m['home'], ai_prob_home, odds_home, edge_home
    else:
        best_pick, best_prob, best_odds, best_edge = m['away'], ai_prob_away, odds_away, edge_away
    
    b = best_odds - 1.0
    kelly_f = (b * best_prob - (1.0 - best_prob)) / b if b > 0 else 0
    stake = max(10, bankroll * kelly_f * kelly_fraction)
    
    if best_edge >= min_edge_slider:
        if cursor:
            cursor.execute("SELECT id FROM ncaa_bets WHERE id = %s", (match_id,))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO ncaa_bets (id, date, match_name, odds, ai_prob, edge, stake, status, profit, home_team, away_team, best_pick)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (match_id, datetime.now().strftime("%Y-%m-%d"), match_name, round(best_odds, 2), best_prob, best_edge, stake, 'Oczekujący', 0.0, m['home'], m['away'], best_pick)
                )
                db_conn.commit()

        analyzed_matches.append({
            **m, "best_pick": best_pick, "ai_prob": best_prob, "odds": round(best_odds, 2), 
            "edge": best_edge, "stake": round(stake, 2), "rat_diff": round(rat_h - rat_a, 1)
        })

if cursor:
    cursor.close()

analyzed_matches.sort(key=lambda x: x['edge'], reverse=True)

# --- INTERFEJS GŁÓWNY ---
st.markdown("## 🏈 College Football Quant Dashboard v5.3 (Pure API)")
tab1, tab2, tab3 = st.tabs(["🔥 Sygnały Value H2H", "🛡️ Diagnostyka API", "📁 Rejestr w Chmurze"])

with tab1:
    st.subheader("Terminal Analityczny (Czyste Dane z API)")
    if not analyzed_matches:
        st.warning("Brak meczów z API spełniających kryteria Edge. Sprawdź zakładkę 'Diagnostyka API', aby zweryfikować stan danych dostarczanych przez serwer.")
    else:
        for m in analyzed_matches:
            edge_pct = round(m['edge'] * 100, 1)
            prob_pct = round(m['ai_prob'] * 100, 1)
            with st.container():
                st.markdown(f"""
                    <div class="value-card">
                        <h3>{m['home']} vs {m['away']} <span class="badge-power">⚡ Diff: {m["rat_diff"]:+} pkt</span></h3>
                        <p style="color: #94a3b8; font-size: 12px;">Start: {m.get('commence_time', 'Brak')}</p>
                        <p>🎯 <b>Typ AI:</b> <span style="color: #60a5fa;">{m['best_pick']}</span> (Kurs: {m['odds']:.2f})</p>
                        <p>🔥 <b>Edge:</b> <span style="color: #3b82f6;">+{edge_pct}%</span> | 💰 <b>Stawka Kellego:</b> {m['stake']} PLN</p>
                    </div>
                """, unsafe_allow_html=True)
                st.progress(m['ai_prob'], text=f"Szanse AI: {prob_pct}%")
                st.divider()

with tab2:
    st.subheader("🛡️ Diagnostyka Połączenia z The Odds API")
    st.markdown(f"""
        <div class="test-box">
        <b>[API Status Code]:</b> {api_status}<br>
        <b>[Liczba surowych obiektów z API]:</b> {raw_count}<br>
        <b>[Przetworzone mecze z kursami]:</b> {len(raw_matches)}<br>
        <b>[Sygnały po filtrze Edge]:</b> {len(analyzed_matches)}<br><br>
        <i>System pracuje wyłącznie na autentycznych danych rynkowych z podanego klucza API i chmury Neon.</i>
        </div>
    """, unsafe_allow_html=True)

with tab3:
    st.subheader("📁 Rejestr Bazy Danych w Chmurze")
    if db_conn:
        df_bets = pd.read_sql("SELECT * FROM ncaa_bets", db_conn)
        st.dataframe(df_bets, use_container_width=True)
    
