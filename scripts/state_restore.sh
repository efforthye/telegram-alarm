#!/bin/bash
# 암호화된 state 브랜치에서 monitor/ 로 상태 파일을 풀어 놓는다.
set -e
PASS=$(printf '%s' "$TELEGRAM_BOT_TOKEN" | sha256sum | cut -d' ' -f1)
if git ls-remote --exit-code --heads origin state >/dev/null; then
  git fetch -q --depth 1 origin state
  rm -rf _old && mkdir -p _old && git --work-tree=_old checkout -q FETCH_HEAD -- . && git reset -q
  if [ -f _old/state.enc ]; then
    openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:$PASS" -in _old/state.enc | tar xz -C monitor || echo "복호화 실패: 새 상태로 시작"
  else
    for f in holdings.json state.json cmd_state.json krx_all.json last_picks.json monitor.log; do
      [ -f "_old/$f" ] && cp "_old/$f" monitor/ || true
    done
  fi
fi
[ -f monitor/holdings.json ] || cp monitor/holdings.seed.json monitor/holdings.json
