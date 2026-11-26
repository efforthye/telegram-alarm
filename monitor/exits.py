#!/usr/bin/env python3
"""보유 종목 매도 판정 (RichClub 메모의 매도 규칙).

판정 단계 (위에서부터 먼저 걸리는 것):
  매도      - 손절선 이탈(매수 신호 때 정한 손절가)
            - 피보나치 78.6% 이탈 (0617: "78.6 깨지면 무조건 도망")
            - 침체: 선행스팬 역배열 + 구름 아래 (0625_1)
            - 60일선 하락 중이고 그 아래 (0625_1: "60일선 하락 구간은 침체")
            - RSI(9) 70 위에서 하방이탈 (0624/0625_1: "무조건 도망")
            - 스토캐스틱 slow 데드크로스 + 음봉 + 거래량 1.5배 이상 (정호 매매법 매도 조건)
            - 전략 B 보유분: 캔들 몸통이 볼린저(18) 상단 아래 (0618_2)
            - 10일선 종가 이탈 (0616/0617: "그냥 나온다", "고민 없이")
            - 5일선 상승하다 꺾임 (0624: "바로 그날 무조건 매도 시그널")
  분할매도  - 전고점 근처에서 돌파 실패 (0617: 전고점 못 뚫으면 나와야)
  버티기    - 손실 중이지만 매도 조건 없음, 78.6% 위 (0617: 지켜보고 물타기 고려 가능)
  정상      - 매도 조건 없음
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "scheduled-tasks", "stock-true-scanner"))
import stock_scan as S  # noqa: E402

LEVEL = {"정상": 0, "버티기": 1, "분할매도": 2, "매도": 3}


BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]


def binance_get(path):
    err = None
    for host in BINANCE_HOSTS:
        try:
            with urllib.request.urlopen(urllib.request.Request(host + path, headers={"User-Agent": "monitor"}),
                                        timeout=20) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"binance 실패 {path}: {err}")


def binance_rows(symbol, limit=400):
    k = binance_get(f"/api/v3/klines?symbol={symbol}USDT&interval=1d&limit={limit}")
    return [(str(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in k]


def check(rows, buy_price=None, stop=None, strategy=None):
    d, o, h, lo, c, v = (list(z) for z in zip(*rows))
    n, i = len(c), len(c) - 1
    if n < 90:
        return {"verdict": "판정 불가", "reasons": ["데이터 부족"]}
    ma10, ma20, ma60 = S.sma(c, 10), S.sma(c, 20), S.sma(c, 60)
    vma20 = S.sma(v, 20)
    r9 = S.rsi(c, 9)
    raw_k = [None] * n
    for t in range(11, n):
        hh, ll = max(h[t - 11:t + 1]), min(lo[t - 11:t + 1])
        raw_k[t] = (c[t] - ll) / (hh - ll) * 100 if hh > ll else 50
    sk = S.sma_opt(raw_k, 3)
    sd = S.sma_opt(sk, 5)

    def mid(t, k):
        return (max(h[t - k + 1:t + 1]) + min(lo[t - k + 1:t + 1])) / 2
    s0 = i - 26
    span_a = (mid(s0, 9) + mid(s0, 26)) / 2
    span_b = mid(s0, 52)

    hi_idx = max(range(i - 59, i + 1), key=lambda t: h[t])
    swing_low = min(lo[max(0, hi_idx - 60):hi_idx + 1])
    fib786 = h[hi_idx] - 0.786 * (h[hi_idx] - swing_low)
    prev_high = max(h[i - 60:i - 5])
    w = c[i - 17:i + 1]
    m = sum(w) / 18
    bb_up = m + 2 * (sum((x - m) ** 2 for x in w) / 18) ** 0.5
    vol_ratio = v[i] / vma20[i] if vma20[i] else 0
    bearish = c[i] < o[i]
    ma60_falling = ma60[i] < ma60[i - 5]

    sell, part = [], []
    if stop and lo[i] <= stop:
        sell.append(f"손절선 {stop:,.6g} 이탈")
    if c[i] < fib786:
        sell.append(f"피보나치 78.6% ({fib786:,.6g}) 이탈")
    if span_a < span_b and c[i] < min(span_a, span_b):
        sell.append("침체 (선행스팬 역배열 + 구름 아래)")
    if ma60_falling and c[i] < ma60[i]:
        sell.append(f"60일선 하락 중이고 그 아래 (60일선 {ma60[i]:,.6g})")
    if any(r9[t - 1] is not None and r9[t - 1] >= 70 > (r9[t] or 0) for t in range(i - 2, i + 1)):
        sell.append("RSI 70 위에서 하방이탈")
    if (sk[i] is not None and sd[i] is not None and sk[i - 1] is not None and sd[i - 1] is not None
            and sk[i - 1] >= sd[i - 1] and sk[i] < sd[i] and bearish and vol_ratio >= 1.5):
        sell.append("스토캐스틱 데드크로스 + 음봉 + 거래량 급증")
    if strategy and "B" in strategy and max(o[i], c[i]) < bb_up:
        sell.append(f"볼린저 상단 ({bb_up:,.6g}) 이탈 (전략 B)")
    # 0616/0617: "10일 이평선 하방이탈하면 그냥 나온다", "이평선 끼면 무조건 나옴, 고민 없이"
    if c[i] < ma10[i]:
        sell.append(f"10일선 ({ma10[i]:,.6g}) 종가 이탈")
    # 0624: "5일선 상승하다가 꺾이는 경우 바로 그날 무조건 매도 시그널"
    ma5 = S.sma(c, 5)
    if ma5[i - 1] > ma5[i - 2] and ma5[i] < ma5[i - 1]:
        sell.append("5일선 상승하다 꺾임")
    if prev_high * 0.97 <= h[i] and c[i] < prev_high and r9[i] is not None and r9[i] < r9[i - 1]:
        part.append(f"전고점 ({prev_high:,.6g}) 근처 돌파 실패")

    pnl = round((c[i] / buy_price - 1) * 100, 1) if buy_price else None
    if sell:
        verdict, reasons = "매도", sell
    elif part:
        verdict, reasons = "분할매도", part
    elif pnl is not None and pnl < 0:
        verdict, reasons = "버티기", [f"매도 조건 없음, 78.6% ({fib786:,.6g}) 위"]
    else:
        verdict, reasons = "정상", ["매도 조건 없음"]
    return {
        "verdict": verdict, "reasons": reasons, "close": c[i], "pnl_pct": pnl,
        "lines": {"fib786": fib786, "ma10": ma10[i], "ma60": ma60[i], "ma60_falling": ma60_falling,
                  "prev_high": prev_high, "stop": stop},
        "rsi9": round(r9[i], 1) if r9[i] is not None else None,
    }
