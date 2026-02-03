# Cryptography Security

## Secure Random Generation

### DO USE
```python
# Python
import secrets
token = secrets.token_urlsafe(32)  # For tokens
random_bytes = secrets.token_bytes(32)  # For keys

# Node.js
const crypto = require('crypto');
const token = crypto.randomBytes(32).toString('hex');

# Go
import "crypto/rand"
b := make([]byte, 32)
rand.Read(b)
```

### NEVER USE
```python
# INSECURE - predictable
import random
token = ''.join(random.choices('abcdef0123456789', k=32))

# INSECURE - time-based seed
random.seed(time.time())
```

## Hashing

### For Passwords
Use bcrypt, argon2, or scrypt — NOT plain hashes.

See [authentication.md](authentication.md) for details.

### For Data Integrity
```python
import hashlib

# SHA-256 for integrity checks
hash = hashlib.sha256(data).hexdigest()

# HMAC for authentication
import hmac
mac = hmac.new(key, message, hashlib.sha256).hexdigest()
```

### NEVER USE
- MD5 (broken)
- SHA1 (deprecated)
- CRC32 (not cryptographic)

## Encryption

### Symmetric (AES)
```python
from cryptography.fernet import Fernet

# Generate key (store securely!)
key = Fernet.generate_key()

# Encrypt
f = Fernet(key)
ciphertext = f.encrypt(plaintext.encode())

# Decrypt
plaintext = f.decrypt(ciphertext).decode()
```

### Key Management
- [ ] Never hardcode keys in source
- [ ] Use environment variables or secrets manager
- [ ] Rotate keys periodically
- [ ] Different keys for different purposes
- [ ] Secure key derivation (PBKDF2, scrypt) from passwords

## Timing Attacks

### Vulnerable Pattern
```python
# VULNERABLE - early exit leaks info
def check_token(provided, expected):
    if len(provided) != len(expected):
        return False
    for i in range(len(provided)):
        if provided[i] != expected[i]:
            return False  # Exits early on first mismatch
    return True
```

### Safe Pattern
```python
import hmac

def check_token(provided, expected):
    return hmac.compare_digest(provided.encode(), expected.encode())
```

### Where Timing Matters
- Password/token comparison
- API key validation
- HMAC verification
- Any secret comparison

## Common Mistakes

### Don't Roll Your Own Crypto
```python
# BAD - custom "encryption"
def encrypt(data, key):
    return ''.join(chr(ord(c) ^ ord(k)) for c, k in zip(data, key * len(data)))

# GOOD - use established library
from cryptography.fernet import Fernet
```

### Don't Reuse Nonces/IVs
```python
# BAD - static IV
cipher = AES.new(key, AES.MODE_CBC, iv=b'0000000000000000')

# GOOD - random IV per encryption
iv = secrets.token_bytes(16)
cipher = AES.new(key, AES.MODE_CBC, iv=iv)
```

### Don't Use ECB Mode
```python
# BAD - ECB shows patterns
cipher = AES.new(key, AES.MODE_ECB)

# GOOD - CBC or GCM
cipher = AES.new(key, AES.MODE_GCM)
```
