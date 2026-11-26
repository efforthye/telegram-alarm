#!/usr/bin/env python3
"""주식 true 스캐너 규칙 시뮬레이션 (여러 방식 비교).

- 매일 장 마감 데이터로 판정 (그날까지의 데이터만 사용)
- 매수: 신호 난 날 종가 (메모: "양봉 확인하고 종가쯤에 산다")
- 매도 (먼저 걸리는 것):
    손절선 터치 -> 손절가(갭 하락이면 시가)
    A 또는 S 포함: 종가 10일선 이탈 -> 그날 종가
    B만: 캔들 몸통 위쪽이 볼린저(18) 상단 아래 -> 그날 종가
    기간 끝까지 보유 -> 마지막 종가로 평가
- 비용 왕복 0.25%, 포트폴리오 1,000만 원, 최대 5종목, 종목당 200만 원
비교 방식:
    AB       : A 또는 B 통과 (강한 자리 무시)
    AB+S표시 : AB와 같은 종목, 순위만 강한 자리 가산 (현재 적용 방식)
    S단독    : A/B/강한 자리 중 하나라도
    *_시장   : 위 방식 + 코스피 60일선 위·상승일 때만
사용법: backtest.py [거래일수=21]
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_scan as S  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 21
COST = 0.0025
CAPITAL, SLOTS = 10_000_000, 5


def flags(a):
    if a["stagnant"] or a["blockers"]:
        return None
    core = a["domain_score"] >= 3 or a["macd_zero_cross"]
    return {"A": a["buy_ok"] == 6 and core, "B": a["bb_ok"] == 3, "S": bool(a.get("strong_hits"))}


def bb18_upper(c, t):
    w = c[t - 17:t + 1]
    m = sum(w) / 18
    return m + 2 * (sum((x - m) ** 2 for x in w) / 18) ** 0.5


def main():
    uni = json.load(open(os.path.join(S.HERE, "universe.json")))
    with ThreadPoolExecutor(max_workers=5) as ex:
        data = dict(zip([u["code"] for u in uni], ex.map(lambda u: S.yahoo(u["yahoo"], "3y"), uni)))
    for u in uni:
        if not data[u["code"]]:
            data[u["code"]] = S.yahoo(u["yahoo"], "3y")
    kospi = S.yahoo("^KS11", "3y")
    kc, kdates = [r[4] for r in kospi], [r[0] for r in kospi]
    dates = kdates[-(DAYS + 1):]
    sig_days = dates[:-1]

    def gate_on(day):
        k = kdates.index(day)
        ma = sum(kc[k - 59:k + 1]) / 60
        return kc[k] > ma and ma > sum(kc[k - 64:k - 4]) / 60

    gate = {d: gate_on(d) for d in sig_days}
    raw = []
    for code, rows in data.items():
        if not rows:
            continue
        idx = {r[0]: k for k, r in enumerate(rows)}
        for day in sig_days:
            k = idx.get(day)
            if k is None or k < 140:
                continue
            try:
                a = S.analyze(rows[:k + 1], fast=True)
            except Exception:  # noqa: BLE001
                continue
            if not a or a["trade_value_eok"] < 10:
                continue
            f = flags(a)
            if f and any(f.values()):
                raw.append({"day": day, "code": code, "f": f, "dscore": a["domain_score"],
                            "rscore": a.get("rank_score", a["domain_score"]),
                            "entry": rows[k][4], "stop": a["levels"]["stop"]["price"]})

    def simulate(sig, strat):
        rows = data[sig["code"]]
        c = [r[4] for r in rows]
        k0 = [r[0] for r in rows].index(sig["day"])
        for k in range(k0 + 1, len(rows)):
            d, o, h, lo, cl, _ = rows[k]
            if lo <= sig["stop"]:
                return d, min(o, sig["stop"]), "손절", True
            if ("A" in strat or "S" in strat) and cl < sum(c[k - 9:k + 1]) / 10:
                return d, cl, "10일선 이탈", True
            if strat == ["B"] and max(o, cl) < bb18_upper(c, k):
                return d, cl, "볼린저 상단 이탈", True
        return rows[-1][0], c[-1], "보유중(평가)", False

    def build(variant):
        out = []
        for s in raw:
            f = s["f"]
            if variant.startswith("S단독"):
                strat = [x for x in ("S", "A", "B") if f[x]]
            else:
                strat = [x for x in ("A", "B") if f[x]]
                if strat and f["S"] and "S표시" in variant:
                    strat = ["S"] + strat
            if not strat or (variant.endswith("_시장") and not gate[s["day"]]):
                continue
            score = s["dscore"] if variant.startswith("AB") and "S표시" not in variant else s["rscore"]
            e = dict(s, strat=strat, score=score)
            e["exit_day"], e["exit"], e["why"], e["closed"] = simulate(e, strat)
            e["ret"] = (e["exit"] / e["entry"] - 1 - COST) * 100
            out.append(e)
        busy, res = {}, []
        for e in sorted(out, key=lambda x: x["day"]):       # 보유 중 재신호 제거
            if busy.get(e["code"], "") >= e["day"]:
                continue
            res.append(e)
            busy[e["code"]] = e["exit_day"]
        return res

    def stats(sigs):
        if not sigs:
            return {"trades": 0}
        r = [s["ret"] for s in sigs]
        return {"trades": len(r), "win_rate": round(sum(x > 0 for x in r) / len(r) * 100, 1),
                "avg_ret": round(sum(r) / len(r), 2), "best": round(max(r), 1), "worst": round(min(r), 1)}

    def portfolio(sigs):
        cash, held, closed = CAPITAL, {}, []
        by_day = {}
        for s in sigs:
            by_day.setdefault(s["day"], []).append(s)
        curve = {}
        for day in dates:
            for code in list(held):
                p = held[code]
                if p["sig"]["exit_day"] == day and p["sig"]["closed"]:
                    cash += p["qty"] * p["sig"]["exit"] * (1 - COST / 2)
                    closed.append(p["sig"])
                    del held[code]
            for s in sorted(by_day.get(day, []), key=lambda x: -x["score"]):
                if len(held) >= SLOTS or s["code"] in held:
                    continue
                qty = int(min(CAPITAL / SLOTS, cash) // (s["entry"] * (1 + COST / 2)))
                if qty <= 0:
                    continue
                cash -= qty * s["entry"] * (1 + COST / 2)
                held[s["code"]] = {"qty": qty, "sig": s}
            val = cash
            for code, p in held.items():
                px = next((r[4] for r in reversed(data[code]) if r[0] <= day), p["sig"]["entry"])
                val += p["qty"] * px
            curve[day] = val
        months = {}
        for d, v in curve.items():
            months[d[:7]] = v
        prev, monthly = CAPITAL, {}
        for m, v in months.items():
            monthly[m] = round((v / prev - 1) * 100, 1)
            prev = v
        peak, mdd = CAPITAL, 0.0
        for v in curve.values():
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        end = list(curve.values())[-1]
        return {"ret_pct": round((end / CAPITAL - 1) * 100, 2), "mdd_pct": round(mdd * 100, 1),
                "trades": len(closed) + len(held), "monthly": monthly}

    variants = ["AB", "AB+S표시", "S단독", "AB_시장", "AB+S표시_시장", "S단독_시장"]
    result = {"period": f"{sig_days[0]} ~ {dates[-1]}", "days": len(sig_days),
              "gate_on_days": sum(gate.values()),
              "kospi_change_pct": round((kc[-1] / kc[kdates.index(sig_days[0])] - 1) * 100, 2),
              "kospi_monthly": {}, "variants": {}}
    prev = kc[kdates.index(sig_days[0])]
    for m in sorted({d[:7] for d in dates}):
        last = [kc[kdates.index(d)] for d in dates if d[:7] == m][-1]
        result["kospi_monthly"][m] = round((last / prev - 1) * 100, 1)
        prev = last
    for v in variants:
        sigs = build(v)
        result["variants"][v] = {
            "trades": stats(sigs),
            "by_strategy": {k: stats([s for s in sigs if k in s["strat"]]) for k in ("S", "A", "B")},
            "exit_reasons": {w: sum(s["why"] == w for s in sigs)
                             for w in ("손절", "10일선 이탈", "볼린저 상단 이탈", "보유중(평가)")},
            "portfolio": portfolio(sigs),
        }
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
