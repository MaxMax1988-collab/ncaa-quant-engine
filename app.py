import streamlit as st
import pandas as pd
import numpy as np
import requests
import sqlite3
from datetime import datetime, timezone, timedelta
from rapidfuzz import process, fuzz

# --- KONFIGURACJA STRONY ---
st.set_page_config(
    page_title="Maksbet NCAA - College Football Quant Engine",
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
    </style>
""", unsafe_allow_html=True)

# --- CONFIG API ---
ODDS_API_KEY = "3e81b37bac9bb7efbe8d2cf68065b6dc"
ODDS_BASE_URL = "https://api.the-odds-api.com/v4/sports"

# Klucz do College Football Data API
CFBD_API_KEY = "Bu754KagVf9KUyZiMidj/5Zbi0ykn4CL16hS04SccbuUk2If+H4RLvGXssgGZ"

# --- HYBRYDOWY SŁOWNIK ALIASÓW ---
TEAM_ALIAS_MAP = {
    "Alabama Crimson Tide": "Alabama",
    "Georgia Bulldogs": "Georgia",
    "Ohio State Buckeyes": "Ohio State",
    "Michigan Wolverines": "Michigan",
    "Texas Longhorns": "Texas",
    "Notre Dame Fighting Irish": "Notre Dame",
    "Oregon Ducks": "Oregon",
    "Penn State Nittany Lions": "Penn State"
}

def normalize_team_name(team_name_from_odds, stats_team_list=None, threshold=70):
    """Hybrydowe dopasowywanie: najpierw słownik stały, potem fuzzy matching."""
    if not team_name_from_odds:
        return "Unknown"
        
    if team_name_from_odds in TEAM_ALIAS_MAP:
        return TEAM_ALIAS_MAP[team_name_from_odds]
    
    if not stats_team_list:
        return team_name_from_odds

    best_match, score, _ = process.extractOne(
        team_name_from_odds, 
        stats_team_list, 
        scorer=fuzz.WRatio
    )
    
    if score >= threshold:
        return best_match
    
    return team_name_from_odds

# --- POBIERANIE STATYSTYK Z COLLEGE FOOTBALL DATA API ---
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_cfbd_stats():
    """Pobiera listę drużyn NCAA FBS z College Football Data API z uwzględnieniem roku"""
    url = "https://api.collegefootballdata.com/teams/fbs"
    headers = {"Authorization": f"Bearer {CFBD_API_KEY}", "accept": "application/json"}
    params = {"year": 2026}
    stats_dict = {}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for team_info in data:
                name = team_info.get("school", "")
                if name:
                    stats_dict[name] = {"win_pct": 0.5}
    except Exception as e:
        pass
    return stats_dict

cfbd_stats_cache = fetch_cfbd_stats()
available_cfbd_teams = list(cfbd_stats_cache.keys())

# --- BAZA DANYCH SQLITE ---
def init_ncaa_db():
    conn = sqlite3.connect("ncaa_bets_database.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ncaa_bets (
            id TEXT PRIMARY KEY,
            date TEXT,
            match_name TEXT,
            odds REAL,
            ai_prob REAL,
            edge REAL,
            stake REAL,
            status TEXT,
            profit REAL,
            home_team TEXT,
            away_team TEXT,
            best_pick TEXT
        )
    ''')
    conn.commit()
    return conn

db_conn = init_ncaa_db()

# --- PANEL BOCZNY ---
with st.sidebar:
    st.markdown("### 🏈 Maksbet NCAA")
    st.caption("College Football Quant Engine")
    st.markdown("---")
    
    bankroll = st.number_input("Kapitał początkowy (PLN)", min_value=100, value=1000, step=100)
    kelly_fraction = st.selectbox("Strategia Kellego", [0.1, 0.25, 0.5], index=1, format_func=lambda x: f"{int(x*100)}% Kellego")
    min_edge_slider = st.slider("Minimalny Edge (%) do akceptacji", min_value=0.0, max_value=10.0, value=1.0, step=0.5) / 100.0
    
    st.markdown("---")
    cursor = db_conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ncaa_bets")
    total_tracked = cursor.fetchone()[0]
    st.metric("Monitorowane zakłady NCAA", total_tracked)

# --- POBIERANIE KURSÓW Z THE ODDS API ---
@st.cache_data(ttl=900, show_spinner=False)
def fetch_ncaa_odds():
    url = f"{ODDS_BASE_URL}/americanfootball_ncaaf/odds"
    params = {"apiKey": ODDS_API_KEY, "regions": "us,eu", "markets": "h2h", "oddsFormat": "decimal"}
    all_m = []
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            for match in res.json():
                match_id = match.get('id')
                commence = match.get('commence_time')
                raw_home = match.get('home_team')
                raw_away = match.get('away_team')
                
                home = normalize_team_name(raw_home, available_cfbd_teams)
                away = normalize_team_name(raw_away, available_cfbd_teams)
                
                oh, oa = 1.90, 1.90
                books = match.get('bookmakers', [])
                if books:
                    for mkt in books[0].get('markets', []):
                        if mkt.get('key') == 'h2h':
                            for out in mkt.get('outcomes', []):
                                out_name = normalize_team_name(out.get('name'), available_cfbd_teams)
                                if out_name == home or out.get('name') == raw_home:
                                    oh = float(out.get('price', 1.90))
                                elif out_name == away or out.get('name') == raw_away:
                                    oa = float(out.get('price', 1.90))
                all_m.append({
                    "id": str(match_id), 
                    "home": home, 
                    "away": away, 
                    "commence_time": commence, 
                    "odds_home": oh, 
                    "odds_away": oa
                })
    except Exception as e:
        pass
    return all_m

raw_matches = fetch_ncaa_odds()
analyzed_matches = []

for m in raw_matches:
    match_id = m['id']
    match_name = f"{m['home']} vs {m['away']}"
    odds_home, odds_away = m['odds_home'], m['odds_away']
    
    prob_raw_h = 1.0 / odds_home if odds_home > 0 else 0.5
    prob_raw_a = 1.0 / odds_away if odds_away > 0 else 0.5
    tot_v = prob_raw_h + prob_raw_a
    implied_h = prob_raw_h / tot_v if tot_v > 0 else 0.5
    
    ai_prob_home = implied_h
    ai_prob_away = 1.0 - ai_prob_home
    
    edge_home = (ai_prob_home * odds_home) - 1.0
    edge_away = (ai_prob_away * odds_away) - 1.0
    
    if edge_home >= edge_away:
        best_pick, best_prob, best_odds, best_edge = m['home'], ai_prob_home, odds_home, edge_home
    else:
        best_pick, best_prob, best_odds, best_edge = m['away'], ai_prob_away, odds_away, edge_away
    
    b = best_odds - 1.0
    kelly_f = (b * best_prob - (1.0 - best_prob)) / b if b > 0 else 0
    stake = max(0, bankroll * kelly_f * kelly_fraction)
    
    if best_edge >= min_edge_slider:
        cursor.execute("SELECT id FROM ncaa_bets WHERE id = ?", (match_id,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO ncaa_bets VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Oczekujący', 0.0, ?, ?, ?)",
                (match_id, datetime.now().strftime("%Y-%m-%d"), match_name, best_odds, best_prob, best_edge, stake, m['home'], m['away'], best_pick)
            )
            db_conn.commit()

        m_copy = m.copy()
        m_copy.update({
            "best_pick": best_pick, 
            "ai_prob": best_prob, 
            "odds": best_odds, 
            "edge": best_edge, 
            "stake": round(stake, 2),
            "implied_h": round(implied_h * 100, 2)
        })
        analyzed_matches.append(m_copy)

analyzed_matches.sort(key=lambda x: x['edge'], reverse=True)

# --- INTERFEJS GŁÓWNY ---
st.markdown("## 🏈 College Football Quant Dashboard")
tab1, tab2, tab3 = st.tabs(["🔥 Sygnały Value NCAA", "🛡️ Diagnostyka", "📁 Rejestr Zakładów NCAA"])

with tab1:
    st.subheader("Terminal Analityczny: NCAA Football")
    if not analyzed_matches:
        st.warning("Brak nadchodzących spotkań NCAA spełniających kryteria dodatniego Value.")
    else:
        for m in analyzed_matches:
            edge_pct = round(m['edge'] * 100, 1)
            prob_pct = round(m['ai_prob'] * 100, 1)
            with st.container():
                st.markdown(f"""
                    <div class="value-card">
                        <h3>{m['home']} vs {m['away']}</h3>
                        <p style="color: #94a3b8; font-size: 12px;">Start: {m.get('commence_time', 'Najbliższe godziny')}</p>
                        <p>🎯 <b>Typ AI:</b> <span style="color: #60a5fa;">{m['best_pick']}</span> (Kurs: {m['odds']})</p>
                        <p>🔥 <b>Value:</b> <span style="color: #3b82f6;">+{edge_pct}%</span> | 💰 <b>Stawka:</b> {m['stake']} PLN</p>
                    </div>
                """, unsafe_allow_html=True)
                st.divider()

with tab2:
    st.subheader("🛡️ Terminal Diagnostyczny NCAA")
    test_api_status = f"✅ OK (Pobrano meczów: {len(raw_matches)})" if raw_matches else "❌ Brak danych"
    cfbd_status = f"✅ OK (Załadowano {len(cfbd_stats_cache)} drużyn FBS)" if cfbd_stats_cache else "❌ Problem z pobieraniem statystyk CFBD"
    
    st.markdown(f"""
        <div class="test-box">
        <b>[TEST 1] Zewnętrzne API Kursów NCAA (The Odds API):</b><br>
        Status: {test_api_status}<br><br>
        <b>[TEST 2] Zewnętrzne API Statystyk (College Football Data):</b><br>
        Status: {cfbd_status}
        </div>
    """, unsafe_allow_html=True)

with tab3:
    st.subheader("📁 Rejestr Zakładów NCAA")
    df_bets = pd.read_sql("SELECT * FROM ncaa_bets", db_conn)
    if df_bets.empty: 
        st.info("Brak zapisanych zakładów NCAA.")
    else: 
        st.dataframe(df_bets, use_container_width=True)
            
