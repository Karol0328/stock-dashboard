from flask import Flask, render_template, jsonify
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import pytz
import requests
import feedparser
import yfinance as yf

app = Flask(__name__)
CORS(app)

# ── stock lists ───────────────────────────────────────────────────────────────

US_TOP10 = [
    ("Apple",       "AAPL"),
    ("NVIDIA",      "NVDA"),
    ("Microsoft",   "MSFT"),
    ("Amazon",      "AMZN"),
    ("Alphabet",    "GOOGL"),
    ("Meta",        "META"),
    ("台積電 ADR",   "TSM"),
    ("Tesla",       "TSLA"),
    ("Broadcom",    "AVGO"),
    ("Berkshire B", "BRK-B"),
]

KR_STOCKS = [
    ("三星電子",  "005930.KS"),
    ("SK 海力士", "000660.KS"),
]

# ── generic stock fetch ───────────────────────────────────────────────────────

def fetch_stock(name: str, symbol: str) -> dict:
    try:
        fi    = yf.Ticker(symbol).fast_info
        price = getattr(fi, "last_price",      None)
        prev  = getattr(fi, "previous_close",  None)
        high  = getattr(fi, "day_high",        None)
        low   = getattr(fi, "day_low",         None)
        vol   = getattr(fi, "last_volume",     None)
        cur   = getattr(fi, "currency",        "")
        chg   = (price - prev) if price and prev else 0
        pct   = (chg / prev * 100) if prev else 0
        return {
            "name": name, "symbol": symbol,
            "price":      round(price, 2) if price else "N/A",
            "change":     round(chg, 2),
            "change_pct": round(pct, 2),
            "high":       round(high, 2) if high else "N/A",
            "low":        round(low,  2) if low  else "N/A",
            "volume":     int(vol) if vol else 0,
            "currency":   cur,
            "positive":   chg >= 0,
        }
    except Exception as e:
        return {"name": name, "symbol": symbol, "price": "N/A",
                "change": 0, "change_pct": 0, "high": "N/A", "low": "N/A",
                "volume": 0, "currency": "", "positive": True, "error": str(e)}


# ── Taiwan Index Futures (台指期, TX) via TAIFEX MIS API ─────────────────────
#
# MarketType=0 → 日盤  (suffix -F), session 08:45–13:45 CST
# MarketType=1 → 夜盤  (suffix -M), session 15:00–05:00 CST next day
# RtData.QuoteList[0] = spot (TXF-S), rest = futures contracts

_TAIFEX_URL  = "https://mis.taifex.com.tw/futures/api/getQuoteList"
_TAIFEX_HDRS = {
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Content-Type": "application/json;charset=UTF-8",
    "Referer":      "https://mis.taifex.com.tw/",
    "Origin":       "https://mis.taifex.com.tw",
}


def _taifex_quote(market_type: str) -> list:
    body = {"MarketType": market_type, "CommodityID": "TX",
            "ContractDateStr": "", "RowOffset": "0"}
    r = requests.post(_TAIFEX_URL, json=body, headers=_TAIFEX_HDRS, timeout=8)
    return r.json().get("RtData", {}).get("QuoteList", [])


def _parse_tx_contract(rows: list, suffix: str) -> dict | None:
    """Return the near-month contract (highest volume, no spread '/', matching suffix)."""
    def _vol(q):
        try: return int(q.get("CTotalVolume") or 0)
        except: return 0

    candidates = [
        q for q in rows
        if q.get("SymbolID", "").endswith(suffix)
        and "/" not in q.get("SymbolID", "")
        and q.get("CLastPrice") not in ("", None)
    ]
    if not candidates:
        return None

    best = max(candidates, key=_vol)

    def _f(k):
        v = best.get(k, "")
        try: return float(str(v).replace(",", "")) if v not in ("", None) else None
        except: return None

    price = _f("CLastPrice")
    ref   = _f("CRefPrice")
    diff  = _f("CDiff")
    pct   = _f("CDiffRate")

    if not price or price < 5000:
        return None

    chg = diff if diff is not None else (price - ref if ref else 0)
    cp  = pct  if pct  is not None else (chg / ref * 100 if ref else 0)

    return {
        "price":      int(price),
        "change":     int(chg),
        "change_pct": round(cp, 2),
        "high":       int(_f("CHighPrice") or 0) or "N/A",
        "low":        int(_f("CLowPrice")  or 0) or "N/A",
        "volume":     int(best.get("CTotalVolume") or 0),
        "positive":   chg >= 0,
        "contract":   best.get("DispCName", ""),
        "date":       best.get("CDate", ""),
        "time":       best.get("CTime", ""),
        "source":     "TAIFEX",
    }


def fetch_tx_futures() -> dict:
    """Return the most recent TX futures price, preferring today's latest session."""
    try:
        day   = _parse_tx_contract(_taifex_quote("0"), "-F")
        night = _parse_tx_contract(_taifex_quote("1"), "-M")

        def recency(d): return (d or {}).get("date", "") + (d or {}).get("time", "")

        result = (day if recency(day) >= recency(night) else night) if day and night \
                 else (day or night)
        if result:
            return result
    except Exception:
        pass
    return {"price": "N/A", "change": 0, "change_pct": 0, "positive": True, "source": "—"}


# ── TXO Options Max Pain ──────────────────────────────────────────────────────
#
# Source: TAIFEX daily CSV via POST https://www.taifex.com.tw/cht/3/optDataDown
# Encoding: MS950 (Big5 superset)
# CSV cols: 0=date, 1=contract, 2=expiry, 3=strike, 4=買/賣權,
#           9=volume, 11=open_interest, 17=session(一般/盤後)
#
# Strategy:
#   - Use near-month MONTHLY contract (no W/F in expiry code, earliest date)
#   - "一般" session rows have end-of-day OI; "盤後" rows do not
#   - Try today first, fall back to previous trading days (up to 3)

_TAIFEX_OPT_URL = "https://www.taifex.com.tw/cht/3/optDataDown"
_TAIFEX_OPT_HDRS = {
    "User-Agent": "Mozilla/5.0",
    "Referer":    "https://www.taifex.com.tw/",
}


def _fetch_txo_csv(date_str: str) -> str | None:
    """Download TXO options CSV for date_str = 'YYYY/MM/DD'. Returns text or None."""
    payload = {"down_type": "1", "commodity_id": "TXO",
               "queryStartDate": date_str, "queryEndDate": date_str}
    r = requests.post(_TAIFEX_OPT_URL, data=payload,
                      headers=_TAIFEX_OPT_HDRS, timeout=15)
    content = r.content.decode("ms950", errors="replace")
    return content if "履約價" in content else None


def _parse_txo_oi(content: str) -> tuple[dict, str]:
    """Parse CSV → {strike: {"call_oi": int, "put_oi": int}}, near_month_str."""
    lines = content.splitlines()

    monthly = {
        p[2].strip() for line in lines[1:] for p in [line.split(",")]
        if len(p) >= 12 and p[2].strip()
        and "W" not in p[2] and "F" not in p[2]
    }
    if not monthly:
        return {}, ""
    near = min(monthly)

    oi_map: dict = {}
    for line in lines[1:]:
        p = line.split(",")
        if len(p) < 18 or p[2].strip() != near or p[17].strip() != "一般":
            continue
        try:
            strike = int(float(p[3].strip()))
            raw    = p[11].strip()
            oi     = int(raw) if raw not in ("-", "", "0") else 0
            side   = p[4].strip()
        except (ValueError, IndexError):
            continue
        bucket = oi_map.setdefault(strike, {"call_oi": 0, "put_oi": 0})
        if side == "買權":   bucket["call_oi"] += oi
        elif side == "賣權": bucket["put_oi"]  += oi

    return oi_map, near


def _calc_max_pain(rows: list) -> int | None:
    """Calculate max pain strike from [{strike, call_oi, put_oi}]."""
    if not rows:
        return None
    min_pain, best = float("inf"), None
    for test in rows:
        k    = test["strike"]
        pain = sum(
            (k - r["strike"]) * (r.get("call_oi") or 0) if k > r["strike"] else 0
            + (r["strike"] - k) * (r.get("put_oi")  or 0) if k < r["strike"] else 0
            for r in rows
        )
        if pain < min_pain:
            min_pain, best = pain, k
    return best


def fetch_txo_max_pain() -> dict:
    """Fetch TXO OI, calculate max pain. Falls back to previous trading days."""
    for days_back in range(4):
        d = datetime.now() - timedelta(days=days_back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y/%m/%d")
        try:
            content = _fetch_txo_csv(date_str)
            if not content:
                continue
            oi_map, near = _parse_txo_oi(content)
            rows = [{"strike": k, **v} for k, v in oi_map.items()
                    if v["call_oi"] + v["put_oi"] > 0]
            if not rows:
                continue
            mp = _calc_max_pain(rows)
            return {
                "max_pain":  mp,
                "strikes":   len(rows),
                "near_month": near,
                "data_date": date_str,
                "label":     "今日" if days_back == 0 else f"{days_back}日前",
                "error":     None,
            }
        except Exception:
            continue
    return {"max_pain": None, "strikes": 0, "error": "no OI data"}


# ── market status ─────────────────────────────────────────────────────────────

def market_status() -> dict:
    now = datetime.now(pytz.utc)

    def is_open(tz_name, oh, om, ch, cm):
        loc = now.astimezone(pytz.timezone(tz_name))
        if loc.weekday() >= 5:
            return False
        t = loc.hour * 60 + loc.minute
        return (oh * 60 + om) <= t < (ch * 60 + cm)

    tw_day   = is_open("Asia/Taipei",      9,  0, 13, 30)
    tx_open  = tw_day or is_open("Asia/Taipei", 15, 0, 23, 59) \
                      or is_open("Asia/Taipei",  0, 0,  5,  0)
    return {
        "taiwan":     tw_day,
        "tx_futures": tx_open,
        "us":         is_open("America/New_York", 9, 30, 16,  0),
        "korea":      is_open("Asia/Seoul",       9,  0, 15, 30),
    }


# ── news ──────────────────────────────────────────────────────────────────────

def fetch_yf_news(symbol: str, n: int = 4) -> list:
    try:
        out = []
        for item in (yf.Ticker(symbol).news or [])[:n]:
            c = item.get("content", {})
            title = c.get("title", "")
            if title:
                out.append({
                    "title":     title,
                    "link":      (c.get("canonicalUrl") or {}).get("url", ""),
                    "published": c.get("pubDate", ""),
                    "source":    (c.get("provider") or {}).get("displayName", ""),
                })
        return out
    except Exception:
        return []


def fetch_gnews(query: str, lang: str = "zh-TW", n: int = 5) -> list:
    try:
        url  = f"https://news.google.com/rss/search?q={query}&hl={lang}&gl=TW&ceid=TW:zh-Hant"
        feed = feedparser.parse(url)
        return [{
            "title":     e.get("title", ""),
            "link":      e.get("link",  ""),
            "published": e.get("published", ""),
            "source":    getattr(getattr(e, "source", None), "title", ""),
        } for e in feed.entries[:n]]
    except Exception:
        return []


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/taiwan")
def api_taiwan():
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_taiex = ex.submit(fetch_stock, "台股加權指數", "^TWII")
        f_tsmc  = ex.submit(fetch_stock, "台積電 (2330)", "2330.TW")
        f_tx    = ex.submit(fetch_tx_futures)
        f_mp    = ex.submit(fetch_txo_max_pain)
    st = market_status()
    return jsonify({
        "taiex":      f_taiex.result(),
        "tsmc":       f_tsmc.result(),
        "tx_futures": f_tx.result(),
        "max_pain":   f_mp.result(),
        "status":     {"day": st["taiwan"], "tx": st["tx_futures"]},
        "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/us")
def api_us():
    with ThreadPoolExecutor(max_workers=12) as ex:
        f_es    = ex.submit(fetch_stock, "S&P 500 期貨 (ES)", "ES=F")
        f_nq    = ex.submit(fetch_stock, "NASDAQ 期貨 (NQ)",  "NQ=F")
        f_top10 = [ex.submit(fetch_stock, n, s) for n, s in US_TOP10]
    st = market_status()
    return jsonify({
        "futures":   [f_es.result(), f_nq.result()],
        "top10":     [f.result() for f in f_top10],
        "status":    st["us"],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/korea")
def api_korea():
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(fetch_stock, n, s) for n, s in KR_STOCKS]
    st = market_status()
    return jsonify({
        "stocks":    [f.result() for f in futures],
        "status":    st["korea"],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/news")
def api_news():
    with ThreadPoolExecutor(max_workers=9) as ex:
        f_tw_g   = ex.submit(fetch_gnews,   "台股 今日", "zh-TW", 6)
        f_us_yf  = ex.submit(fetch_yf_news, "^GSPC", 3)
        f_us_g   = ex.submit(fetch_gnews,   "US stock market", "en-US", 3)
        f_ss_yf  = ex.submit(fetch_yf_news, "005930.KS", 3)
        f_ss_g   = ex.submit(fetch_gnews,   "三星電子 股票", "zh-TW", 3)
        f_hx_yf  = ex.submit(fetch_yf_news, "000660.KS", 3)
        f_hx_g   = ex.submit(fetch_gnews,   "SK海力士 股票", "zh-TW", 3)
        f_hanta  = ex.submit(fetch_gnews,   "hantavirus", "en-US", 8)
    return jsonify({
        "taiwan":     f_tw_g.result(),
        "us":         f_us_yf.result() + f_us_g.result(),
        "samsung":    f_ss_yf.result() + f_ss_g.result(),
        "hynix":      f_hx_yf.result() + f_hx_g.result(),
        "hantavirus": f_hanta.result(),
        "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001, threaded=True)
