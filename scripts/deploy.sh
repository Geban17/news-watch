#!/bin/bash
# Выкатка Новостного сторожа с мака: копирует ТОЛЬКО в /opt/news-watch и перезапускает ТОЛЬКО news-watch.
#   scripts/deploy.sh
# Стены проверяются здесь, а не обещаются:
#   * дерево чистое, тесты зелёные, ломание без расхождений — до копирования;
#   * службы боя и их PID до и после выкатки одинаковы (бой не задет);
#   * pip на сервере — только если requirements.txt изменился, только готовыми колёсами (без сборки на боевом сервере),
#     под nice / ionice;
#   * токена ещё нет (.env) — служба ставится, но не стартует: ждёт bin/set_token Олега.
set -euo pipefail
SERVER="${NEWS_SERVER:-root@209.250.234.38}"
REMOTE=/opt/news-watch
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
BATTLE="ambush-live ambush-2h@oleg ambush-watchdog ambush-tg-admin paper-g0607 hq-bot"
cd "$ROOT"
die() { echo "ОТКАЗ: $*" >&2; exit 1; }

echo "1/6 дерево чистое"
[ -z "$(git status --porcelain -- news_watch tests scripts systemd bin requirements.txt)" ] || die "незакоммиченные правки"

echo "2/6 тесты и ломание"
"$PY" -m pytest -q -p no:cacheprovider tests | tail -1
"$PY" scripts/break_check.py tests/breaks.json | tail -1

echo "3/6 сервер: нагрузка и бой до выкатки"
ssh -o BatchMode=yes "$SERVER" 'awk "{exit !(\$1 < 1.0)}" /proc/loadavg' || die "load ≥ 1.0 — правило №1, не выкатываю"
pids() { ssh -o BatchMode=yes "$SERVER" "for u in $BATTLE; do printf '%s %s %s\n' \$u \$(systemctl is-active \$u) \$(systemctl show \$u -p MainPID --value); done"; }
BEFORE="$(pids)"
echo "$BEFORE" | grep -qv " active " && die "бой нездоров — рядом не выкатываю:
$BEFORE"

echo "4/6 копирую"
REQ_MD5="$(md5 -q requirements.txt)"
COPYFILE_DISABLE=1 tar --no-xattrs -czf - news_watch requirements.txt systemd | ssh -o BatchMode=yes "$SERVER" "
set -euo pipefail
id news >/dev/null 2>&1 || useradd --system --home $REMOTE --shell /usr/sbin/nologin news
mkdir -p $REMOTE/state
rm -rf $REMOTE/news_watch
tar -xzf - -C $REMOTE
find $REMOTE/news_watch -name __pycache__ -prune -exec rm -rf {} +
[ -d $REMOTE/.venv ] || nice -n 19 python3 -m venv $REMOTE/.venv
if [ \"\$(cat $REMOTE/.venv/.req_md5 2>/dev/null)\" != \"$REQ_MD5\" ]; then
    nice -n 19 ionice -c3 timeout 600 $REMOTE/.venv/bin/pip install -q --only-binary=:all: -r $REMOTE/requirements.txt
    echo $REQ_MD5 > $REMOTE/.venv/.req_md5
    echo '   библиотеки поставлены'
else
    echo '   библиотеки те же'
fi
chown -R root:root $REMOTE/news_watch $REMOTE/.venv $REMOTE/systemd
chown news:news $REMOTE/state && chmod 750 $REMOTE/state
install -m 644 $REMOTE/systemd/news-watch.service /etc/systemd/system/news-watch.service
systemctl daemon-reload
"

echo "5/6 служба"
ssh -o BatchMode=yes "$SERVER" "
if [ -f $REMOTE/.env ]; then
    systemctl enable -q news-watch && systemctl restart news-watch && sleep 5
    echo \"   news-watch: \$(systemctl is-active news-watch), память \$(systemctl show news-watch -p MemoryCurrent --value) Б\"
else
    echo '   токена нет — служба поставлена, не запущена: Олег запускает bin/set_token'
fi"

echo "6/6 бой после выкатки"
AFTER="$(pids)"
[ "$BEFORE" = "$AFTER" ] || die "службы боя изменились во время выкатки:
до:
$BEFORE
после:
$AFTER"
echo "   бой не тронут: $(echo "$AFTER" | wc -l | tr -d ' ') служб, PID те же"
echo "готово"
