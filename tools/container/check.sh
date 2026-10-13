#!/bin/bash
# The CI checks, inside a test box (box.sh check <name>): lint, the UI's
# JavaScript, every free test suite, the car picker against the box's own
# sample index, and a first start driven in Chromium. Free: no network, no
# API key. Prints one ok/FAIL line per check and exits non-zero on any FAIL.
cd /work
rc=0
ruff check --select F . > /tmp/ruff.log 2>&1 && echo "ok   ruff" || { echo "FAIL ruff"; cat /tmp/ruff.log; rc=1; }
python tools/check_js.py > /tmp/js.log 2>&1 && echo "ok   check_js" || { echo "FAIL check_js"; tail -5 /tmp/js.log; rc=1; }
for p in tests/test_*.py; do
    t=$(basename "$p")
    [ "$t" = test_ui_picks.py ] && continue
    s=$SECONDS
    if python "$p" > "/tmp/$t.log" 2>&1; then echo "ok   $t ($((SECONDS - s)) s)"
    else echo "FAIL $t"; tail -8 "/tmp/$t.log"; rc=1; fi
done
s=$SECONDS
python tests/test_ui_picks.py --index .dev/index.db > /tmp/picks.log 2>&1 \
    && echo "ok   test_ui_picks ($((SECONDS - s)) s): $(tail -1 /tmp/picks.log)" \
    || { echo "FAIL test_ui_picks"; tail -15 /tmp/picks.log; rc=1; }
# first start: a second, empty instance, driven in Chromium; screenshots in
# /tmp/ui-shots (box.sh fetch <name> /tmp/ui-shots)
AUTORUNDOWN_HOME=/tmp/ui-home python serve.py --host 127.0.0.1 --port 8759 > /tmp/ui-server.log 2>&1 &
for _ in $(seq 60); do curl -sf http://127.0.0.1:8759/api/stats > /dev/null && break; sleep 0.5; done
s=$SECONDS
python packaging/windows/ui_check.py --url http://localhost:8759 --corpus sample_corpus --shots /tmp/ui-shots > /tmp/ui.log 2>&1 \
    && echo "ok   ui_check ($((SECONDS - s)) s)" || { echo "FAIL ui_check"; tail -15 /tmp/ui.log; rc=1; }
curl -s -X POST http://127.0.0.1:8759/api/shutdown > /dev/null || true
[ $rc = 0 ] && echo "all checks passed" || echo "SOME CHECKS FAILED"
exit $rc
