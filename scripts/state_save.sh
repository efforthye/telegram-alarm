#!/bin/bash
# monitor/ 의 상태 파일을 암호화해서 state 브랜치에 커밋 1개로 덮어쓴다.
set -e
PASS=$(printf '%s' "$TELEGRAM_BOT_TOKEN" | sha256sum | cut -d' ' -f1)
[ -f monitor/monitor.log ] && tail -n 1000 monitor/monitor.log > monitor/monitor.log.tmp && mv monitor/monitor.log.tmp monitor/monitor.log
FILES=$(cd monitor && ls holdings.json state.json cmd_state.json krx_all.json last_picks.json monitor.log 2>/dev/null || true)
rm -rf _new && mkdir -p _new
tar cz -C monitor $FILES | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "pass:$PASS" -out _new/state.enc
cd _new
git init -q
git checkout -q -b state
git config user.name "alarm-bot"
git config user.email "alarm-bot@users.noreply.github.com"
git add state.enc
git commit -q -m "state (encrypted)"
git push -q -f "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" state
