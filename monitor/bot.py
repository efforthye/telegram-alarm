#!/usr/bin/env python3
"""상시 실행 봇: 텔레그램 명령에 바로 답하고, 정해진 시각에 감시/브리핑을 돌린다.

- 텔레그램 롱폴링(10초)으로 명령을 기다렸다가 바로 처리
- 감시(monitor.py)와 브리핑은 별도 프로세스로 돌려서, 그동안에도 명령에 답할 수 있게 함
    평일 09:00~15:40 KST: 30분마다 감시 (주식+코인+매도)
    그 외: 1시간마다 감시 (코인+매도)
    평일 08:30 아침 브리핑, 16:30 마감 브리핑, 주말 09:00 코인 브리핑
- 10분마다, 그리고 끝날 때 상태를 암호화해서 저장 (scripts/state_save.sh)
- BOT_SECONDS(기본 5시간 40분) 뒤 종료. 워크플로가 다음 실행을 이어서 띄운다.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import monitor as M  # noqa: E402

HD = M.HD
KST = timezone(timedelta(hours=9))
DEADLINE = time.time() + float(os.environ.get("BOT_SECONDS", 5 * 3600 + 40 * 60))
CMD_STATE = os.path.join(HERE, "cmd_state.json")
SAVE_EVERY = 600


def load_cst():
    try:
        return json.load(open(CMD_STATE))
    except Exception:  # noqa: BLE001
        return {}


def save_cst(cst):
    json.dump(cst, open(CMD_STATE, "w"), ensure_ascii=False)


def due_job(now, done):
    """지금 돌려야 할 작업 (key, args) 하나. 없으면 None."""
    d, wd, hm = now.strftime("%Y-%m-%d"), now.weekday(), now.hour * 100 + now.minute
    jobs = []
    if wd < 5 and 830 <= hm < 900:
        jobs.append((f"morning-{d}", ["--report", "morning"]))
    if wd < 5 and 1630 <= hm < 1700:
        jobs.append((f"close-{d}", ["--report", "stock"]))
    if wd >= 5 and 900 <= hm < 930:
        jobs.append((f"weekend-{d}", ["--report", "coin"]))
    if wd < 5 and 900 <= hm <= 1545:
        jobs.append((f"mon-{d}-{now.hour}-{now.minute // 30}", []))
    else:
        jobs.append((f"mon-{d}-{now.hour}", []))
    for key, args in jobs:
        if key not in done:
            return key, args
    return None


def start(args):
    env = dict(os.environ, BOT_MODE="1")
    return subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "monitor.py"), *args],
                            cwd="/tmp", env=env)


def save_state():
    try:
        subprocess.run(["bash", os.path.join(ROOT, "scripts", "state_save.sh")], cwd=ROOT,
                       timeout=120, capture_output=True)
    except Exception as e:  # noqa: BLE001
        M.log(f"bot save ERR {str(e)[:100]}")


def main():
    cst = load_cst()
    done = set(cst.get("done_keys", []))
    job, job_key = None, None
    last_save = time.time()
    M.log("bot start")
    while time.time() < DEADLINE:
        # 1) 명령: 최대 10초 기다렸다가 바로 처리
        if not os.environ.get("TELEGRAM_BOT_TOKEN"):
            time.sleep(10)
        try:
            n = HD.poll_commands(cst, M.notify, timeout=10)
            if n:
                M.log(f"bot commands {n}")
        except Exception as e:  # noqa: BLE001
            M.log(f"bot poll ERR {str(e)[:100]}")
            time.sleep(5)
        # 2) 끝난 작업 정리
        if job and job.poll() is not None:
            done.add(job_key)
            job, job_key = None, None
        # 3) 할 작업 시작 (한 번에 하나)
        if not job:
            nxt = due_job(datetime.now(KST), done)
            if nxt:
                job_key, args = nxt
                job = start(args)
        # 4) 상태 저장
        cst["done_keys"] = sorted(done)[-200:]
        save_cst(cst)
        if time.time() - last_save > SAVE_EVERY and not job:
            save_state()
            last_save = time.time()
    if job:
        job.wait(timeout=600)
        done.add(job_key)
    cst["done_keys"] = sorted(done)[-200:]
    save_cst(cst)
    M.log("bot stop")


if __name__ == "__main__":
    main()
