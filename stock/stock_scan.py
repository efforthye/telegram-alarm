#!/usr/bin/env python3
"""국내 주식 true 스캐너 (RichClub 팀 메모의 차트매매 규칙 기반).

대상: 코스피200 + 코스닥150 (universe.json), 시세: Yahoo Finance 일봉.
장 마감(15:30 KST) 이후 실행하는 것을 전제로 한다.

규칙 출처 (RichClub/document):
  - 스토캐스틱, macd 매매법_정호.txt : 매수 6조건
      양봉 / 5·20일선 정배열 또는 골든크로스 / 캔들이 60일선 위(또는 상향돌파)
      / MACD 정배열 / 스토캐스틱 slow(12,5) 정배열 / OBV(9) 정배열
  - 0618_1_혜림.md : 도메인 점수 가중치, 신호 필터 (macd_zero_cross 또는 점수 >= 3)
  - 0625_1.md : 침체 = 선행스팬1·2 역배열 + 캔들이 구름 아래 -> 매매 금지
  - 0617.md / 0616.md : 60일선 아래는 볼 가치 없음, 10일선 종가 이탈 시 매도,
      RSI(9) 70 위에서 하방이탈 시 매도, 이평선 간격 넓으면 상승파 짧음,
      전고점 돌파 실패는 훼이크, 피보나치 78.6% 깨지면 도망,
      "매수점 대비 손실률을 감당할 수 있으면 진입"
  - 0622.md : 미국 증시 전날 상승 시 다음날 국내 상승 확률 높음, 환율·유가 상승은 악재

판정:
  TRUE : 시장 게이트 통과 + 매수 6조건 전부 + 침체 아님 + 도메인 점수 >= 3(또는 MACD 0선 돌파)
  후보 : 위 종목 조건은 통과했지만 시장 게이트 실패
  근접 : 매수 6조건 중 5개 + 침체 아님 + 도메인 점수 >= 3
가격/기간/손절은 과거 데이터로 계산한 참고선이며 예측이 아니다.
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------- 데이터
def yahoo(symbol, rng="2y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                res = json.load(r)["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            rows = []
            for i, ts in enumerate(res.get("timestamp") or []):
                o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
                if None in (o, h, l, c) or c <= 0:
                    continue
                d = datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d")
                rows.append((d, float(o), float(h), float(l), float(c), float(v or 0)))
            return rows
        except Exception:  # noqa: BLE001
            time.sleep(1 + attempt)
    return []


# ---------------------------------------------------------------- 지표
def sma(x, n):
    out = [None] * len(x)
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def sma_opt(x, n):
    """None이 섞인 시계열의 단순이동평균."""
    out = [None] * len(x)
    for i in range(len(x)):
        w = x[max(0, i - n + 1):i + 1]
        if len(w) == n and None not in w:
            out[i] = sum(w) / n
    return out


def ema(x, n):
    k = 2 / (n + 1)
    out, prev = [], None
    for v in x:
        prev = v if prev is None else prev + k * (v - prev)
        out.append(prev)
    return out


def rsi(c, n=9):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g, l = g / n, l / n
    out[n] = 100 - 100 / (1 + g / l) if l else 100
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + g / l) if l else 100
    return out


def analyze(rows, name="", fast=False):
    d = [r[0] for r in rows]
    o = [r[1] for r in rows]
    h = [r[2] for r in rows]
    lo = [r[3] for r in rows]
    c = [r[4] for r in rows]
    v = [r[5] for r in rows]
    n = len(c)
    i = n - 1
    if n < 130:
        return None

    ma5, ma10, ma20, ma60, ma120 = (sma(c, k) for k in (5, 10, 20, 60, 120))
    vma20 = sma(v, 20)

    # RSI(9) + 시그널(9)
    r9 = rsi(c, 9)
    r9sig = sma_opt(r9, 9)

    # MACD: 12/25, 시그널 = MACD의 60일 이동평균 (0617/0618 메모: "macd 12/25/60", "시그널 선 60일 이평")
    e12, e25 = ema(c, 12), ema(c, 25)
    macd = [a - b for a, b in zip(e12, e25)]
    macd_sig = [x if x is not None else macd[k] for k, x in enumerate(sma(macd, 60))]

    # 스토캐스틱 slow: K 12일(3일 평활), D 5일
    raw_k = [None] * n
    for t in range(11, n):
        hh, ll = max(h[t - 11:t + 1]), min(lo[t - 11:t + 1])
        raw_k[t] = (c[t] - ll) / (hh - ll) * 100 if hh > ll else 50
    sk = sma_opt(raw_k, 3)
    sd = sma_opt(sk, 5)

    # OBV + 9일 시그널
    obv = [0.0]
    for t in range(1, n):
        obv.append(obv[-1] + (v[t] if c[t] > c[t - 1] else -v[t] if c[t] < c[t - 1] else 0))
    obv_sig = sma(obv, 9)

    # 볼린저(20, 2)
    bb_w = [None] * n
    for t in range(19, n):
        w = c[t - 19:t + 1]
        m = sum(w) / 20
        sdv = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
        bb_w[t] = 4 * sdv / m if m else None

    # 볼린저(18, 2) + BWI(18) — 0618_2 메모 "일주일에 10%" 매매법
    bb18_up, bb18_bwi = [None] * n, [None] * n
    for t in range(17, n):
        w = c[t - 17:t + 1]
        m = sum(w) / 18
        sdv = (sum((x - m) ** 2 for x in w) / 18) ** 0.5
        bb18_up[t] = m + 2 * sdv
        bb18_bwi[t] = 4 * sdv / m if m else None

    # 일목균형표: 오늘 위치의 구름 = 26일 전에 계산한 선행스팬
    def mid(a, b, t, k):
        return (max(a[t - k + 1:t + 1]) + min(b[t - k + 1:t + 1])) / 2

    def cloud_at(t):
        s = t - 26
        if s < 52:
            return None, None
        tenkan, kijun = mid(h, lo, s, 9), mid(h, lo, s, 26)
        return (tenkan + kijun) / 2, mid(h, lo, s, 52)

    span_a, span_b = cloud_at(i)
    cloud_top, cloud_bot = max(span_a, span_b), min(span_a, span_b)
    pa3, pb3 = cloud_at(i - 3)

    # ATR14
    trs = [max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1])) for t in range(n - 14, n)]
    atr = sum(trs) / 14

    vol_ratio = v[i] / vma20[i] if vma20[i] else 0
    bullish = c[i] > o[i]

    # ---------------- 침체/제외
    stagnant = span_a < span_b and c[i] < cloud_bot
    ma60_slope = (ma60[i] / ma60[i - 5] - 1) * 100

    # ---------------- 매수 6조건 (정호 매매법)
    golden_5_20 = ma5[i - 1] <= ma20[i - 1] and ma5[i] > ma20[i]
    cross_up_60 = c[i - 1] <= ma60[i - 1] and c[i] > ma60[i]
    buy = {
        "양봉": bullish,
        "5·20일선 정배열/골든크로스": ma5[i] > ma20[i] or golden_5_20,
        "60일선 위(또는 상향돌파)": c[i] > ma60[i] or cross_up_60,
        "MACD 정배열": macd[i] > macd_sig[i],
        "스토캐스틱 정배열": sk[i] is not None and sd[i] is not None and sk[i] > sd[i],
        "OBV 정배열": obv_sig[i] is not None and obv[i] > obv_sig[i],
    }

    # ---------------- 볼린저·OBV 매매법 (0618_2 메모)
    # 매수: 캔들이 볼린저 상단선에 닿은 첫날 + OBV 골든크로스 또는 정배열 + BWI 상향 중
    # 매도: 캔들 몸통(시가·종가 중 높은 쪽)이 상단선에서 떨어지면 매도
    first_touch = h[i] >= bb18_up[i] and h[i - 1] < bb18_up[i - 1]
    obv_ok = obv_sig[i] is not None and obv[i] > obv_sig[i]
    bwi_up = bb18_bwi[i] is not None and bb18_bwi[i - 1] is not None and bb18_bwi[i] > bb18_bwi[i - 1]
    bb_checks = {
        "볼린저(18) 상단 첫 터치": first_touch,
        "OBV(9) 골든크로스/정배열": obv_ok,
        "BWI(18) 상향": bwi_up,
    }

    # ---------------- 도메인 점수 (혜림 모델 가중치)
    macd_zero_cross = any(macd[t - 1] <= 0 < macd[t] for t in range(i - 2, i + 1))
    ma60_support = (min(lo[i - 2:i + 1]) <= ma60[i] * 1.02 and c[i] > ma60[i] and bullish)
    uptrend_with_volume = (ma5[i] > ma10[i] > ma20[i] > ma60[i]) and vol_ratio >= 1.5
    ichi_breakout = c[i] > cloud_top and pa3 is not None and c[i - 3] <= max(pa3, pb3)
    vol_surge_bullish = vol_ratio >= 2 and bullish
    recent_w = [x for x in bb_w[i - 119:i + 1] if x is not None]
    bb_squeeze = bool(recent_w) and bb_w[i] <= sorted(recent_w)[max(0, len(recent_w) // 5 - 1)]
    rsi_cooling = (max(x for x in r9[i - 10:i] if x is not None) >= 70
                   and 45 <= r9[i] <= 60 and r9[i] > r9[i - 1])
    # 쌍바닥(간이): 최근 40일 안 두 저점이 3% 이내, 오른쪽이 더 높고, 지금 두 저점 사이 고점 위
    double_bottom = False
    win = lo[i - 40:i - 2]
    if len(win) == 38:
        a_idx = min(range(0, 19), key=lambda k: win[k])
        b_idx = min(range(19, 38), key=lambda k: win[k])
        a, b = win[a_idx], win[b_idx]
        neck = max(h[i - 40 + a_idx:i - 40 + b_idx + 1])
        double_bottom = b >= a and (b - a) / a <= 0.03 and c[i] > neck and vol_ratio >= 1.2

    # ---------------- 메모의 "고민 없이 사야 하는 자리" (강한 매수)
    def resample(key_fn):
        out = {}
        for t in range(n):
            k = key_fn(d[t])
            if k not in out:
                out[k] = [o[t], h[t], lo[t], c[t]]
            else:
                bar = out[k]
                bar[1], bar[2], bar[3] = max(bar[1], h[t]), min(bar[2], lo[t]), c[t]
        return list(out.values())

    def aligned(bars, a, b, cc):
        cl = [x[3] for x in bars]
        if len(cl) < cc + 1:
            return False
        m = lambda k, back=0: sum(cl[len(cl) - k - back:len(cl) - back]) / k  # noqa: E731
        return (m(a) > m(b) > m(cc) and m(a) > m(a, 1) and m(b) > m(b, 1)
                and bars[-1][3] > bars[-1][0])

    wk = resample(lambda s_: datetime.strptime(s_, "%Y-%m-%d").strftime("%G-%V"))
    mo = resample(lambda s_: s_[:7])
    daily_aligned = ma5[i] > ma20[i] > ma60[i] and bullish and ma20[i] > ma20[i - 1]
    tri_frame = daily_aligned and aligned(wk, 5, 20, 60) and aligned(mo, 5, 10, 20)

    # 골든보: 60일선 상승 중, 최근 15일 안에 60일선을 살짝 터치(±2%)하고 U자로 턴, 2일 연속 상승 양봉
    ma60_rising = ma60[i] > ma60[i - 5] > ma60[i - 10]
    touched = any(lo[t] <= ma60[t] * 1.02 for t in range(i - 15, i - 1))
    golden_bo = (ma60_rising and touched and c[i] > ma60[i] * 1.01
                 and c[i] > c[i - 1] > c[i - 2] and bullish)

    # 이평선 밀집 후 정배열 출발: 5·10·20·60일선이 3% 안에 모였다가 최근 3일 안에 정배열 완성 + 거래량
    mas_now = [ma5[i], ma10[i], ma20[i], ma60[i]]
    tight = max(mas_now) / min(mas_now) - 1 <= 0.03
    newly_aligned = (ma5[i] > ma10[i] > ma20[i] > ma60[i]
                     and not all(ma5[t] > ma10[t] > ma20[t] > ma60[t] for t in range(i - 3, i)))
    ma_squeeze_go = tight and newly_aligned and bullish and vol_ratio >= 1.2

    # 기준선 돌파 + 정배열 전환: 종가가 오늘 일목 기준선(26) 위로, 5·20일선 정배열 3일 이내 전환
    kijun_now, kijun_prev = mid(h, lo, i, 26), mid(h, lo, i - 1, 26)
    kijun_break = (c[i - 1] <= kijun_prev < kijun_now <= c[i] or (c[i - 1] <= kijun_prev and c[i] > kijun_now))
    turn_aligned = any(ma5[t - 1] <= ma20[t - 1] and ma5[t] > ma20[t] for t in range(i - 2, i + 1))
    kijun_go = kijun_break and turn_aligned and bullish

    strong_items = {
        "일봉/주봉/월봉 동시 정배열 양봉": tri_frame,
        "골든보(60일선 U자 지지 후 턴)": golden_bo,
        "이평선 밀집 후 정배열 출발": ma_squeeze_go,
        "쌍바닥 확인(오른쪽 저점 더 높음)": double_bottom,
        "기준선 돌파 + 정배열 전환": kijun_go,
    }
    strong_hits = [k for k, ok in strong_items.items() if ok]

    domain_items = {
        "MACD 0선 돌파": (macd_zero_cross, 4),
        "쌍바닥 확인": (double_bottom, 3),
        "60일선 지지": (ma60_support, 3),
        "거래량 동반 정배열": (uptrend_with_volume, 3),
        "일목 구름 돌파": (ichi_breakout, 3),
        "거래량 급증 양봉": (vol_surge_bullish, 2),
        "볼린저 수축": (bb_squeeze, 2),
        "RSI 식힘 후 재상승": (rsi_cooling, 1),
    }
    domain_score = sum(w for ok, w in domain_items.values() if ok)
    domain_hits = [k for k, (ok, _) in domain_items.items() if ok]

    # ---------------- 경고 (훼이크/매도 신호)
    warnings = []
    if c[i] < ma60[i] and ma60_slope < 0:
        warnings.append("60일선 하락 중 + 그 아래")
    if cloud_bot <= c[i] <= cloud_top:
        warnings.append("일목 구름 안(훼이크 구간)")
    spread = (ma5[i] / ma60[i] - 1) * 100
    if spread > 15:
        warnings.append(f"이평선 간격 넓음(5-60일 {spread:.0f}%, 상승파 짧을 수 있음)")
    if r9[i] is not None and r9[i] >= 70:
        warnings.append("RSI 과열(70+)")
    if ma60_slope < 0 and c[i] >= ma60[i]:
        warnings.append("60일선 아직 하락 중(턴 시도 구간)")
    upper_wick = (h[i] - max(o[i], c[i])) / (h[i] - lo[i]) if h[i] > lo[i] else 0
    if upper_wick >= 0.5:
        warnings.append(f"긴 윗꼬리(고가 대비 {(c[i] / h[i] - 1) * 100:.1f}%, 매도 압력)")
    if any(r9[t - 1] is not None and r9[t - 1] >= 70 > r9[t] for t in range(i - 2, i + 1)):
        warnings.append("RSI 70 하방이탈(매도 신호)")
    if c[i] < ma10[i] and c[i - 1] >= ma10[i - 1]:
        warnings.append("10일선 종가 이탈(매도 신호)")
    prev_high = max(h[i - 60:i - 5])
    if prev_high * 0.97 <= c[i] < prev_high:
        warnings.append("전고점 바로 아래(돌파 실패 시 훼이크)")
    gaps = sum(1 for t in range(i - 19, i + 1) if o[t] > h[t - 1] * 1.03)
    if gaps >= 3:
        warnings.append("갭상승 잦음(잡주/세력주 주의)")
    day_chg = (c[i] / c[i - 1] - 1) * 100
    if day_chg >= 10:
        warnings.append(f"급등 당일 +{day_chg:.0f}%(진입 늦음, 추격 주의)")
    # 메모에서 '사면 안 되는 자리'로 명시한 경고 -> TRUE 불가
    blockers = [w for w in warnings if w.startswith((
        "60일선 하락", "일목 구름 안", "이평선 간격 넓음", "RSI 70 하방이탈",
        "10일선 종가 이탈", "급등 당일"))]

    # ---------------- 가격 시나리오
    h60, h120 = max(h[i - 59:i + 1]), max(h[i - 119:i + 1])
    target1 = h60 if h60 > c[i] * 1.01 else c[i] + atr * 7 ** 0.5
    target2 = h120 if h120 > target1 * 1.01 else target1 + atr * 7 ** 0.5
    # 피보나치 78.6%: 최근 60일 고점과 그 이전 저점 사이
    hi_idx = max(range(i - 59, i + 1), key=lambda t: h[t])
    swing_low = min(lo[max(0, hi_idx - 60):hi_idx + 1])
    fib786 = h[hi_idx] - 0.786 * (h[hi_idx] - swing_low)
    low20 = min(lo[i - 19:i + 1])
    # 손절: 종가에서 최소 1 ATR 아래에 있는 지지선 중 가장 가까운 것 (-1%)
    supports = {"피보나치 78.6%": fib786, "20일 저점": low20, "20일선": ma20[i], "60일선": ma60[i]}
    below = {k: x for k, x in supports.items() if x <= c[i] - atr}
    if below:
        stop_basis = max(below, key=below.get)
        stop = below[stop_basis] * 0.99
    else:
        stop_basis, stop = "종가-1.5×ATR", c[i] - 1.5 * atr
    exit_line = ma10[i]

    def pct(x):
        return round((x / c[i] - 1) * 100, 1)

    def hit(move, days):
        k = hits = 0
        for t in range(n - 250 if n > 260 else 1, n - days):
            base = c[t]
            if move >= 0:
                ok = max(h[t + 1:t + 1 + days]) >= base * (1 + move / 100)
            else:
                ok = min(lo[t + 1:t + 1 + days]) <= base * (1 + move / 100)
            k += 1
            hits += ok
        return round(hits / k * 100) if k else None

    def horizon(p):
        m = (p / c[i] - 1) * 100
        return {"hit_5d": hit(m, 5), "hit_10d": hit(m, 10), "hit_20d": hit(m, 20)}

    risk = c[i] - stop
    levels = {
        "close": c[i],
        "target1": {"price": round(target1), "pct": pct(target1), "basis": "60일 고점(전고점)" if h60 > c[i] * 1.01 else "종가+1주 변동폭"},
        "target2": {"price": round(target2), "pct": pct(target2)},
        "exit_line_ma10": {"price": round(exit_line), "pct": pct(exit_line), "rule": "종가가 10일선 아래로 마감하면 매도"},
        "stop": {"price": round(stop), "pct": pct(stop), "basis": stop_basis},
        "reward_risk": round((target1 - c[i]) / risk, 2) if risk > 0 else None,
        "time": {} if fast else {"target1": horizon(target1), "stop": horizon(stop)},
    }

    return {
        "date": d[i],
        "close": c[i],
        "chg_pct": round((c[i] / c[i - 1] - 1) * 100, 2),
        "trade_value_eok": round(c[i] * v[i] / 1e8, 1),
        "buy_checks": buy,
        "buy_ok": sum(buy.values()),
        "bb_checks": bb_checks,
        "bb_ok": sum(bb_checks.values()),
        "bb_exit_upper": round(bb18_up[i]),
        "bb_exit_rule": "캔들 몸통(시가·종가 중 높은 쪽)이 볼린저(18) 상단선 아래로 떨어지면 매도",
        "stagnant": stagnant,
        "domain_score": domain_score,
        # 순위 점수: 도메인 점수에서 위험 요소를 뺀 값
        "strong_hits": strong_hits,
        "rank_score": domain_score + 3 * len(strong_hits)
        - 3 * (ma60_slope < 0)
        - 2 * (r9[i] is not None and r9[i] >= 70)
        - 2 * (upper_wick >= 0.5)
        - 1 * (spread > 10),
        "domain_hits": domain_hits,
        "macd_zero_cross": macd_zero_cross,
        "warnings": warnings,
        "blockers": blockers,
        "indicators": {
            "rsi9": round(r9[i], 1) if r9[i] is not None else None,
            "stoch_k": round(sk[i], 1) if sk[i] is not None else None,
            "macd": round(macd[i], 2), "macd_signal": round(macd_sig[i], 2),
            "vol_ratio_20": round(vol_ratio, 2),
            "ma60_slope_5d_pct": round(ma60_slope, 2),
            "ma5_ma60_spread_pct": round(spread, 1),
        },
        "levels": levels,
    }


# ---------------------------------------------------------------- 시장
def market():
    out = {}
    for key, sym in [("kospi", "^KS11"), ("kosdaq", "^KQ11"), ("nasdaq", "^IXIC"),
                     ("sp500", "^GSPC"), ("usdkrw", "KRW=X"), ("wti", "CL=F")]:
        rows = yahoo(sym, "1y")
        if len(rows) < 70:
            out[key] = None
            continue
        c = [r[4] for r in rows]
        ma60 = sum(c[-60:]) / 60
        ma60_prev = sum(c[-65:-5]) / 60
        out[key] = {
            "date": rows[-1][0], "close": round(c[-1], 2),
            "chg_1d": round((c[-1] / c[-2] - 1) * 100, 2),
            "chg_5d": round((c[-1] / c[-6] - 1) * 100, 2),
            "above_ma60": c[-1] > ma60, "ma60_rising": ma60 > ma60_prev,
        }
    k = out.get("kospi")
    gate = bool(k and k["above_ma60"] and k["ma60_rising"])
    notes = []
    if out.get("nasdaq"):
        notes.append(f"나스닥 전일 {out['nasdaq']['chg_1d']:+.2f}%")
    if out.get("usdkrw") and out["usdkrw"]["chg_5d"] > 1:
        notes.append("환율 5일 +1% 이상(악재)")
    if out.get("wti") and out["wti"]["chg_5d"] > 5:
        notes.append("유가 5일 +5% 이상(악재)")
    return {"gate_ok": gate, "detail": out, "notes": notes,
            "gate_rule": "코스피 종가 > 60일선 그리고 60일선 상승 중"}


# ---------------------------------------------------------------- 메인
def main():
    uni = json.load(open(os.path.join(HERE, "universe.json")))
    # 휴대폰에서 등록한 관심 종목 추가 (holdings.json의 watch)
    try:
        hold = json.load(open(os.path.join(os.path.dirname(HERE), "live-monitor", "holdings.json")))
        codes = {u["code"] for u in uni}
        uni += [dict(w, index="관심") for w in hold.get("watch", []) if w["code"] not in codes]
    except Exception:  # noqa: BLE001
        pass
    mk = market()
    today = datetime.now(KST).strftime("%Y-%m-%d")

    def work(s):
        rows = yahoo(s["yahoo"])
        try:
            a = analyze(rows, s["name"]) if rows else None
        except Exception as e:  # noqa: BLE001
            return s, {"error": str(e)}
        return s, a

    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(work, uni))
    # 조회 실패한 종목은 한 번 더 (순차, 간격 두고)
    for k, (s_, a_) in enumerate(results):
        if not a_ or "error" in a_:
            time.sleep(0.5)
            results[k] = work(s_)

    passed, near, errors, latest = [], [], 0, None
    for s, a in results:
        if not a or "error" in a:
            errors += 1
            continue
        latest = max(latest or a["date"], a["date"])
        row = {"code": s["code"], "name": s["name"], "index": s["index"], "yahoo": s["yahoo"], **a}
        if a["trade_value_eok"] < 10:      # 거래대금 10억 미만 제외
            continue
        # 전략 A: 정호 매매법 6조건 + 혜림 도메인 필터
        core = (not a["stagnant"]) and (a["domain_score"] >= 3 or a["macd_zero_cross"])
        a_pass = a["buy_ok"] == 6 and core
        a_near = core and a["buy_ok"] == 5
        # 전략 B: 볼린저·OBV 매매법 3조건 (침체 아님)
        b_pass = a["bb_ok"] == 3 and not a["stagnant"]
        b_near = a["bb_ok"] == 2 and a["bb_checks"]["볼린저(18) 상단 첫 터치"] and not a["stagnant"]
        s_pass = bool(a["strong_hits"]) and not a["stagnant"] and (a_pass or b_pass)  # 강한 자리는 A/B 통과 종목에만 붙는 표시
        row["strategies"] = [s for s, ok in (("S(강한 자리)", s_pass), ("A(6조건)", a_pass),
                                             ("B(볼린저/OBV)", b_pass)) if ok]
        nb = len(a["blockers"])
        if row["strategies"] and nb == 0:
            passed.append(row)
        elif (row["strategies"] and nb == 1) or ((a_near or b_near) and nb == 0):
            row["near_reason"] = ("차단 경고 1개" if row["strategies"]
                                  else "A 5/6" if a_near else "B 2/3")
            near.append(row)

    key = lambda r: (r["rank_score"], r["levels"]["reward_risk"] or 0)  # noqa: E731
    passed.sort(key=key, reverse=True)
    near.sort(key=key, reverse=True)
    json.dump({
        "run_date_kst": today,
        "data_date": latest,
        "market_open_today": latest == today,
        "market": mk,
        "verdict_label": "TRUE" if mk["gate_ok"] else "후보",
        "passed": passed,
        "near": near[:10],
        "scanned": len(uni),
        "errors": errors,
    }, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
