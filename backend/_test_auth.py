import json
import random
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else b""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def get(path, token):
    headers = {"Authorization": "Bearer " + token}
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


un = "u%d" % random.randint(10000, 99999)
print("TEST_USER", un)

st, reg = post("/api/auth/register", {"username": un, "password": "secret123"})
print("REGISTER", st, "user=", reg.get("user", {}).get("username"), "tokenLen=", len(reg.get("access_token", "")))
token = reg.get("access_token", "")

st, me = get("/api/auth/me", token)
print("ME", st, "user=", me.get("username"))

st, _ = post("/api/auth/login", {"username": un, "password": "wrong"})
print("BAD_LOGIN_EXPECT_401", st)

st, _ = post("/api/auth/logout", {}, token)
print("LOGOUT", st)

st, _ = get("/api/auth/me", token)
print("ME_AFTER_LOGOUT_EXPECT_401", st)
