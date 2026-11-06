#!/usr/bin/env python3
"""Log in locally and create a short-lived PAT for load tests.
The token is written to ~/.deerflow_pat (mode 600) and never printed."""
import getpass, http.cookiejar, json, os, urllib.error, urllib.parse, urllib.request

BASE = os.environ.get("DF_BASE", "http://127.0.0.1:8001")
email = input("登录邮箱: ").strip()
password = getpass.getpass("密码(不显示): ")

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


st, txt = call("POST", "/api/v1/auth/login/local",
               {"username": email, "password": password, "remember_me": "true"}, form=True)
print("登录状态码:", st)
if st != 200:
    print("登录失败:", txt[:200])
    raise SystemExit(1)

csrf = next((c.value for c in jar if c.name == "csrf_token"), None)
print("cookie 名称:", sorted(c.name for c in jar), "| 有 csrf:", bool(csrf))
if not csrf:
    raise SystemExit("没有拿到 csrf_token cookie")

st, txt = call("POST", "/api/v1/auth/pats",
               {"name": "bench", "expires_in_days": 7,
                "scopes": ["threads:read", "threads:write", "runs:create", "runs:read", "runs:cancel"]},
               headers={"X-CSRF-Token": csrf})
print("创建令牌状态码:", st)
if st != 201:
    print("创建失败:", txt[:300])
    raise SystemExit(1)

tok = json.loads(txt)["token"]
fd = os.open(os.path.expanduser("~/.deerflow_pat"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(tok)
print("已保存。前缀:", tok[:4], "长度:", len(tok))
