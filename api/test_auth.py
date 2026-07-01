"""Self-check for HMAC auth token logic. Run: python test_auth.py"""
import hmac, hashlib, time

SECRET = "test-secret-123"
USERNAME = "mamá"


def make_token(username, secret, ttl=86400):
    expiry = str(int(time.time()) + ttl)
    payload = f"{username}:{expiry}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_token(token, secret):
    parts = token.rsplit(":", 2)
    assert len(parts) == 3, "token must have 3 parts"
    username, expiry, sig = parts
    expected = hmac.new(
        secret.encode(), f"{username}:{expiry}".encode(), hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(sig, expected), "signature mismatch"
    assert int(expiry) >= time.time(), "token expired"
    return username


# Valid token round-trip
token = make_token(USERNAME, SECRET)
assert verify_token(token, SECRET) == USERNAME

# Tampered token fails
try:
    verify_token(token + "x", SECRET)
    assert False, "should have failed"
except AssertionError:
    pass

# Wrong secret fails
try:
    verify_token(token, "wrong-secret")
    assert False, "should have failed"
except AssertionError:
    pass

# Expired token fails
expired = make_token(USERNAME, SECRET, ttl=-1)
try:
    verify_token(expired, SECRET)
    assert False, "should have failed"
except AssertionError:
    pass

print("OK - all auth checks passed")
