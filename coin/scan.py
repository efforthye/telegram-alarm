#!/usr/bin/env python3
"""코인 true/false 스캐너.

바이낸스 공개 시세로 거래량 상위 USDT 코인을 훑어서,
사용자가 정한 'true' 조건을 통과하는 코인을 찾는다.

조건 (일봉 확정 종가 기준, 진행 중인 오늘 봉은 제외):
  시장 게이트: BTC 종가 >= $60,000
  코인 조건:
    1) 7일 수익률이 BTC보다 높음 (비트코인보다 덜 빠지거나 더 오름)
    2) 30일 수익률이 BTC보다 높음
    3) 최근 3일 연속 종가가 20일 이동평균 위
    4) 펌핑 제외: 24시간 +25% 이상, 또는 7일 +60% 이상이면 제외
  판정:
    시장 게이트 통과 + 코인 조건 전부 통과 -> TRUE
    시장 게이트 실패 + 코인 조건 전부 통과 -> 후보 (시장이 FALSE라 대기)

결과는 JSON으로 stdout에 출력한다. 판단/알림은 이 스크립트를 부르는 쪽에서 한다.
"""
import json
import os
import sys
import time
import urllib.request

# LIVE=1 이면 진행 중인 오늘 봉도 포함(장중 실시간 판정)
LIVE = os.environ.get("LIVE") == "1"
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]  # 미국 서버(깃허브)에서는 api.binance.com 차단
MIN_QUOTE_VOL = 5_000_000    # 24시간 거래대금 500만 달러 이상만
TOP_N = 80                   # 거래대금 상위 80개만 검사
BTC_GATE = 60_000
PUMP_24H = 25.0
PUMP_7D = 60.0

EXCLUDE_BASES = {
    "USDC", "FDUSD", "TUSD", "DAI", "USDE", "USDP", "BUSD", "EUR", "EURI",
    "AEUR", "USD1", "XUSD", "BFUSD", "RLUSD", "PYUSD", "PAXG", "XAUT", "WBTC", "WBETH",
    "BTC",
}

STOCK_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "SOXS", "SOXL", "TQQQ", "SQQQ",
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO", "TSM",
    "NFLX", "AMD", "INTC", "COIN", "MSTR", "HOOD", "CRCL", "PLTR", "ORCL", "CRM",
    "JPM", "BAC", "GS", "WMT", "COST", "LLY", "UNH", "BRK", "MU", "ARM", "SMCI",
    "BABA", "NKE", "DIS", "KO", "PEP", "MCD", "V", "MA", "PYPL", "SHOP", "UBER",
}


def get(path):
    last_err = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(host + path, headers={"User-Agent": "coin-scanner"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"요청 실패 {path}: {last_err}")


def get_fapi(path):
    req = urllib.request.Request("https://fapi.binance.com" + path,
                                 headers={"User-Agent": "coin-scanner"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def confirmed_closes(symbol, limit=40):
    rows = get(f"/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}")
    now_ms = time.time() * 1000
    # 종료시각([6])이 지난 봉만 확정 봉
    return [float(r[4]) for r in rows if LIVE or r[6] < now_ms]


def confirmed_klines(symbol, limit):
    rows = get(f"/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}")
    now_ms = time.time() * 1000
    return [r for r in rows if LIVE or r[6] < now_ms]


def price_levels(symbol):
    """기계적으로 계산한 가격 시나리오 (예측이 아님).

    - 상단 목표1: 최근 30일 고점
    - 상단 목표2: 최근 90일 고점 (30일 고점과 같으면 30일 고점 + 1주 예상폭)
    - 하단 지지1: 20일 이동평균
    - 하단 지지2: 최근 30일 저점
    - 1주 예상 범위: 종가 ± ATR14 × √7 (최근 변동성이 이어진다고 가정한 대략의 폭)
    - 손익비: (목표1 - 종가) / (종가 - 지지1)
    """
    rows = confirmed_klines(symbol, 400)
    hi = [float(r[2]) for r in rows]
    lo = [float(r[3]) for r in rows]
    cl = [float(r[4]) for r in rows]
    qv = [float(r[7]) for r in rows]
    close = cl[-1]
    trs = []
    for i in range(1, len(rows)):
        trs.append(max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])))
    atr = sum(trs[-14:]) / 14
    week = atr * 7 ** 0.5
    ma20 = sum(cl[-20:]) / 20
    h30, l30 = max(hi[-30:]), min(lo[-30:])
    h90 = max(hi[-90:])
    t2 = h90 if h90 > h30 * 1.01 else h30 + week
    up = h30 - close
    down = close - ma20
    rr = round(up / down, 2) if down > 0 else None

    def pct(x):
        return round((x / close - 1) * 100, 1)

    # --- 기간 기준 ---
    # 과거 이 코인 가격에서, 매일 "그날 종가 대비 +X%"가 N일 안에 고가로 찍혔는지 센 비율.
    # X는 오늘 기준 목표/지지까지 거리를 그대로 쓴다. (그 코인의 과거 변동성 기준 기본 확률)
    def hit_rate(move_pct, days):
        n = hits = 0
        for i in range(len(cl) - days):
            base = cl[i]
            if move_pct >= 0:
                ok = max(hi[i + 1:i + 1 + days]) >= base * (1 + move_pct / 100)
            else:
                ok = min(lo[i + 1:i + 1 + days]) <= base * (1 + move_pct / 100)
            n += 1
            hits += ok
        return round(hits / n * 100) if n else None

    def horizon(price):
        move = (price / close - 1) * 100
        # 변동성만으로 본 대략의 도달 일수: (거리 / 하루 변동폭)^2
        est_days = round((abs(price - close) / atr) ** 2, 1) if atr else None
        return {
            "est_days": est_days,
            "hit_7d": hit_rate(move, 7),
            "hit_14d": hit_rate(move, 14),
            "hit_30d": hit_rate(move, 30),
        }

    # --- 손절(무효화) 기준 ---
    # 구조 손절: 지금 가격 아래 있는 가장 가까운 지지(20일선 또는 30일 저점)를 2% 밑으로 깨면
    #            'TRUE 조건 3(20일선 위)'이 무너진 것으로 본다.
    # 변동성 손절: 종가 - 1.5 × ATR14 (하루 흔들림에 털리지 않을 최소 거리)
    # 둘 중 더 가까운 쪽이 2.5 ATR 이내면 그것을, 아니면 변동성 손절을 쓴다.
    struct_base = ma20 if ma20 < close else l30
    stop_struct = struct_base * 0.98
    stop_atr = close - 1.5 * atr
    stop = stop_struct if close - stop_struct <= 2.5 * atr else stop_atr
    stop = max(stop, 0)
    risk = close - stop
    rr_stop = round((h30 - close) / risk, 2) if risk > 0 else None
    rr_stop_t2 = round((t2 - close) / risk, 2) if risk > 0 else None

    # --- 보조 지표 ---
    gains = [max(cl[i] - cl[i - 1], 0) for i in range(len(cl) - 14, len(cl))]
    losses = [max(cl[i - 1] - cl[i], 0) for i in range(len(cl) - 14, len(cl))]
    ag, al = sum(gains) / 14, sum(losses) / 14
    rsi = round(100 - 100 / (1 + ag / al), 1) if al else 100.0
    vol_ratio = round((sum(qv[-7:]) / 7) / (sum(qv[-30:]) / 30), 2)
    ma50 = sum(cl[-50:]) / 50 if len(cl) >= 50 else None
    ma200 = sum(cl[-200:]) / 200 if len(cl) >= 200 else None

    deriv = {}
    try:
        p = get_fapi(f"/fapi/v1/premiumIndex?symbol={symbol}")
        deriv["funding_pct"] = round(float(p["lastFundingRate"]) * 100, 4)
        oi = get_fapi(f"/futures/data/openInterestHist?symbol={symbol}&period=1d&limit=8")
        if len(oi) >= 8:
            deriv["oi_7d_change_pct"] = round(
                (float(oi[-1]["sumOpenInterestValue"]) / float(oi[0]["sumOpenInterestValue"]) - 1) * 100, 1)
    except Exception:  # noqa: BLE001
        deriv = {"note": "선물 없음 또는 조회 실패"}

    return {
        "close": close,
        "target1_30d_high": {"price": h30, "pct": pct(h30)},
        "target2": {"price": round(t2, 8), "pct": pct(t2),
                    "basis": "90일 고점" if h90 > h30 * 1.01 else "30일 고점+1주 변동폭"},
        "support1_ma20": {"price": round(ma20, 8), "pct": pct(ma20)},
        "support2_30d_low": {"price": l30, "pct": pct(l30)},
        "week_range": {"low": round(close - week, 8), "high": round(close + week, 8),
                       "pct": round(week / close * 100, 1)},
        "reward_risk": rr,
        "stop": {
            "price": round(stop, 8), "pct": pct(stop),
            "basis": ("20일선 -2%" if struct_base == ma20 else "30일 저점 -2%")
                     if stop == stop_struct else "종가 - 1.5×ATR",
            "structure": {"price": round(stop_struct, 8), "pct": pct(stop_struct)},
            "atr": {"price": round(stop_atr, 8), "pct": pct(stop_atr)},
            "rr_to_target1": rr_stop,
            "rr_to_target2": rr_stop_t2,
        },
        "time": {
            "stop": horizon(stop),
            "target1": horizon(h30),
            "target2": horizon(t2),
            "support1": horizon(ma20),
            "support2": horizon(l30),
        },
        "indicators": {
            "rsi14": rsi,
            "volume_7d_vs_30d": vol_ratio,
            "ma50": {"price": round(ma50, 8), "pct": pct(ma50)} if ma50 else None,
            "ma200": {"price": round(ma200, 8), "pct": pct(ma200)} if ma200 else None,
            **deriv,
        },
        "days_of_data": len(rows),
    }


def ret(closes, days):
    if len(closes) <= days:
        return None
    return (closes[-1] / closes[-1 - days] - 1) * 100


def korean_krw_listings():
    """업비트/빗썸 원화(KRW) 마켓에 상장된 코인 심볼 집합. 실패하면 None."""
    out = {}
    for name, url in [("업비트", "https://api.upbit.com/v1/market/all"),
                      ("빗썸", "https://api.bithumb.com/v1/market/all")]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "coin-scanner",
                                                       "accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                rows = json.load(r)
            out[name] = {m["market"].split("-")[1] for m in rows
                         if m["market"].startswith("KRW-")}
        except Exception:  # noqa: BLE001
            out[name] = None
    return out


def main():
    kr = korean_krw_listings()
    kr_ok = any(v for v in kr.values())
    btc = confirmed_closes("BTCUSDT", 80)
    btc_close = btc[-1]
    btc_ma60 = sum(btc[-60:]) / 60
    btc_ma60_prev = sum(btc[-65:-5]) / 60
    btc7, btc30 = ret(btc, 7), ret(btc, 30)
    # 시장 게이트 (엄격): BTC 종가 > 60일선 그리고 60일선 상승 중
    gate = btc_close > btc_ma60 and btc_ma60 > btc_ma60_prev

    tickers = get("/api/v3/ticker/24hr")
    cands = []
    for t in tickers:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if not (base.isascii() and base.isalnum()):
            continue
        if base in EXCLUDE_BASES or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        # 바이낸스 토큰화 주식/ETF(예: SPYB, GOOGLB, SOXSB) 제외
        if base.endswith("B") and base[:-1] in STOCK_TICKERS:
            continue
        qv = float(t["quoteVolume"])
        if qv < MIN_QUOTE_VOL:
            continue
        # 국내(업비트/빗썸 원화 마켓)에서 살 수 없는 코인 제외
        if kr_ok and not any(v and base in v for v in kr.values()):
            continue
        cands.append((qv, s, float(t["priceChangePercent"]), float(t["lastPrice"])))
    cands.sort(reverse=True)
    cands = cands[:TOP_N]

    passed, near = [], []
    errors = []
    for qv, s, chg24, last in cands:
        try:
            c = confirmed_closes(s)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{s}: {e}")
            continue
        if len(c) < 31:
            continue
        # 스테이블코인 제외: 30일 동안 가격이 3% 안에서만 움직였으면 건너뜀
        w = c[-31:]
        if max(w) / min(w) < 1.03:
            continue
        r7, r30 = ret(c, 7), ret(c, 30)
        ma20 = [sum(c[i - 19:i + 1]) / 20 for i in range(len(c) - 3, len(c))]
        above3 = all(c[-3 + k] > ma20[k] for k in range(3))
        rs7 = r7 - btc7
        rs30 = r30 - btc30
        # 펌핑: 24시간 +25% 이상, 7일 +60% 이상, 또는 최근 3개 확정 일봉 중 하루라도 +25% 이상
        day_moves = [(c[i] / c[i - 1] - 1) * 100 for i in range(len(c) - 3, len(c))]
        pump = chg24 >= PUMP_24H or r7 >= PUMP_7D or max(day_moves) >= PUMP_24H
        # 장중 급락: 지금 가격이 어제 확정 종가보다 10% 이상 낮음 -> 판정 보류
        intraday = (last / (c[-2] if LIVE else c[-1]) - 1) * 100
        dumping = intraday <= -10
        checks = {
            "rs7": rs7 > 0,
            "rs30": rs30 > 0,
            "above_ma20_3d": above3,
            "not_pump": not pump,
            "not_dumping_now": not dumping,
        }
        row = {
            "symbol": s[:-4],
            "kr_exchanges": [n for n, v in kr.items() if v and s[:-4] in v],
            "close": c[-1],
            "ret7": round(r7, 1),
            "ret30": round(r30, 1),
            "rs7_vs_btc": round(rs7, 1),
            "rs30_vs_btc": round(rs30, 1),
            "chg24": round(chg24, 1),
            "now_price": last,
            "now_vs_close_pct": round(intraday, 1),
            "max_day_move_3d": round(max(day_moves), 1),
            "quote_vol_musd": round(qv / 1e6, 1),
            "checks": checks,
        }
        n_ok = sum(checks.values())
        if n_ok == len(checks):
            passed.append(row)
        elif (n_ok == len(checks) - 1 and checks["not_pump"]
              and checks["not_dumping_now"]):
            near.append(row)
        time.sleep(0.05)

    key = lambda r: r["rs30_vs_btc"] + r["rs7_vs_btc"]  # noqa: E731
    passed.sort(key=key, reverse=True)
    near.sort(key=key, reverse=True)
    near = near[:10]
    for row in passed + near:
        try:
            row["levels"] = price_levels(row["symbol"] + "USDT")
        except Exception as e:  # noqa: BLE001
            row["levels"] = {"error": str(e)}

    out = {
        "btc": {"close": btc_close, "ret7": round(btc7, 1), "ret30": round(btc30, 1),
                "gate_ok": gate, "ma60": round(btc_ma60, 1),
                "ma60_rising": btc_ma60 > btc_ma60_prev, "above_60k": btc_close >= BTC_GATE},
        "verdict_label": "TRUE" if gate else "후보",
        "passed": passed,
        "near": near,
        "scanned": len(cands),
        "kr_filter": ("업비트/빗썸 원화 마켓 상장 코인만" if kr_ok
                      else "국내 상장 목록 조회 실패 - 필터 없이 스캔함"),
        "errors": errors[:5],
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    # 사용법: scan.py            -> 전체 스캔
    #         scan.py --levels SOL -> 해당 코인의 가격 시나리오만 출력
    if len(sys.argv) == 3 and sys.argv[1] == "--levels":
        sym = sys.argv[2].upper()
        json.dump(price_levels(sym if sym.endswith("USDT") else sym + "USDT"),
                  sys.stdout, ensure_ascii=False, indent=1)
    else:
        main()
