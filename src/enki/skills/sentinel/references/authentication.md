# Authentication Security

## Password Storage

### ONLY USE
```python
# Python - argon2 (preferred) or bcrypt
from argon2 import PasswordHasher
ph = PasswordHasher()
hash = ph.hash(password)
ph.verify(hash, password)

# bcrypt alternative
import bcrypt
hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
bcrypt.checkpw(password.encode(), hash)
```

### NEVER USE
- MD5, SHA1, SHA256 alone (no salt, too fast)
- Custom hashing schemes
- Encryption (reversible) instead of hashing

## Session Management

### Secure Session Configuration
```python
# Flask
app.config['SESSION_COOKIE_SECURE'] = True      # HTTPS only
app.config['SESSION_COOKIE_HTTPONLY'] = True    # No JS access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'   # CSRF protection
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour

# Django
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
```

### Session Token Requirements
- [ ] Generated with cryptographically secure random
- [ ] At least 128 bits of entropy
- [ ] Regenerated after authentication
- [ ] Invalidated on logout (server-side)
- [ ] Not in URL (referer leakage)

## Token Security

### JWT Pitfalls
```python
# BAD - algorithm confusion attack
jwt.decode(token, key, algorithms=["HS256", "RS256", "none"])

# GOOD - explicit algorithm
jwt.decode(token, key, algorithms=["RS256"])

# Check expiration
claims = jwt.decode(token, key, algorithms=["RS256"])
if claims['exp'] < time.time():
    raise ExpiredTokenError
```

### Token Checklist
- [ ] Verify signature before trusting claims
- [ ] Explicit algorithm whitelist
- [ ] Check expiration (`exp` claim)
- [ ] Validate issuer (`iss` claim)
- [ ] Short-lived access tokens (15 min)
- [ ] Refresh tokens stored securely, rotated on use

## Timing Attacks on Auth

### Problem
```python
# VULNERABLE - early exit reveals valid username
if user is None:
    return "Invalid"  # Fast
if not check_password(password, user.hash):
    return "Invalid"  # Slow (hash comparison)
```

### Solution
```python
# SAFE - constant time
import hmac
import secrets

def authenticate(username, password):
    user = User.query.filter_by(username=username).first()

    # Always compute hash even if user doesn't exist
    if user is None:
        # Hash against dummy to maintain timing
        dummy_hash = get_dummy_hash()
        check_password(password, dummy_hash)
        return None

    if check_password(password, user.password_hash):
        return user
    return None

def constant_time_compare(a, b):
    return hmac.compare_digest(a.encode(), b.encode())
```

## Account Security Features

### Required for Production
- [ ] Rate limiting on login (5 attempts / 15 min)
- [ ] Account lockout after N failures
- [ ] Password complexity requirements
- [ ] Breach password checking (haveibeenpwned API)
- [ ] Secure password reset (time-limited tokens)
- [ ] MFA support (TOTP, WebAuthn)
