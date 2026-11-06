#!/usr/bin/env python3
"""Create a throwaway admin on a FRESH DeerFlow database and mint a PAT.
Usage: ab_setup.py RUN_DIR   (writes RUN_DIR/pat, mode 600). Prints status codes only."""
import http.cookiejar, json, os, secrets, sys, urllib.error, urllib.parse, urllib.request

BASE = os.environ.get("DF_BASE", "http://127.0.0.1:8001")
run_dir = sys.argv[1]
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, data=None, headers=None, form=False):
    h = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        with op.open(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


st, txt = call("GET", "/api/v1/auth/setup-status")
print("setup-status:", st, txt[:80])
try:
    needs = json.loads(txt).get("needs_setup")
except Exception:
    needs = None
if needs is not True:
    print("不是全新数据库(needs_setup 不为 true),退出")
    sys.exit(2)

email = "bench-%s@example.com" % secrets.token_hex(4)
password = secrets.token_urlsafe(18)
st, txt = call("POST", "/api/v1/auth/initialize", {"email": email, "password": password})
print("initialize:", st)
if st != 201:
    print(txt[:200])
    sys.exit(1)

st, txt = call("POST", "/api/v1/auth/login/local",
               {"username": email, "password": password, "remember_me": "true"}, form=True)
print("login:", st)
if st != 200:
    print(txt[:200])
    sys.exit(1)
csrf = next((c.value for c in jar if c.name == "csrf_token"), None)
if not csrf:
    print("没有拿到 csrf_token")
    sys.exit(1)

st, txt = call("POST", "/api/v1/auth/pats",
               {"name": "bench", "expires_in_days": 1,
                "scopes": ["threads:read", "threads:write", "runs:create", "runs:read", "runs:cancel"]},
               headers={"X-CSRF-Token": csrf})
print("pat:", st)
if st != 201:
    print(txt[:300])
    sys.exit(1)
tok = json.loads(txt)["token"]
fd = os.open(os.path.join(run_dir, "pat"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(tok)
print("令牌已保存,前缀:", tok[:4], "长度:", len(tok))
