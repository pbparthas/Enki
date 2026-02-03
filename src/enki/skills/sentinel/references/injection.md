# Injection Vulnerabilities

## SQL Injection

### Defense: Parameterized Queries (ALWAYS)
```python
# BAD
cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")

# GOOD
cursor.execute("SELECT * FROM users WHERE name = ?", [name])
```

### Bypass Techniques to Know
- **Comment truncation**: `admin'--`
- **UNION-based**: `' UNION SELECT password FROM users--`
- **Blind boolean**: `' AND 1=1--` vs `' AND 1=2--`
- **Time-based blind**: `' AND SLEEP(5)--`
- **Second-order**: Payload stored, executed later
- **Encoding bypasses**: URL encoding, Unicode, hex

### ORM Pitfalls
```python
# STILL VULNERABLE - raw queries in ORM
User.objects.raw(f"SELECT * FROM users WHERE name = '{name}'")
User.query.filter(text(f"name = '{name}'"))

# SAFE
User.objects.filter(name=name)
User.query.filter(User.name == name)
```

## Cross-Site Scripting (XSS)

### Types
1. **Reflected**: Input immediately echoed back
2. **Stored**: Payload saved, executed for other users
3. **DOM-based**: Client-side JavaScript processes malicious data

### Defense by Context

| Context | Defense |
|---------|---------|
| HTML body | HTML entity encode (`&lt;` `&gt;` `&amp;` `&quot;` `&#x27;`) |
| HTML attribute | Attribute encode + quote attributes |
| JavaScript | JSON.stringify() or avoid entirely |
| URL | URL encode |
| CSS | CSS escape or avoid user data in CSS |

### React-Specific
```jsx
// DANGEROUS - bypasses React's protection
<div dangerouslySetInnerHTML={{__html: userInput}} />

// SAFE - React auto-escapes
<div>{userInput}</div>
```

### Bypass Techniques
- **Case variation**: `<ScRiPt>`
- **Event handlers**: `<img onerror="alert(1)">`
- **SVG**: `<svg onload="alert(1)">`
- **Data URLs**: `<a href="data:text/html,<script>...">`
- **Unicode**: `\u003cscript\u003e`
- **Mutation XSS**: Payload mutates during parsing

## Server-Side Request Forgery (SSRF)

### The Attack
Server makes HTTP request to attacker-controlled URL, can reach internal services.

### Defense
```python
# BAD
response = requests.get(user_provided_url)

# BETTER - allowlist
ALLOWED_HOSTS = ['api.trusted.com', 'cdn.trusted.com']
parsed = urlparse(user_provided_url)
if parsed.hostname not in ALLOWED_HOSTS:
    abort(400)
response = requests.get(user_provided_url, allow_redirects=False)

# BEST - fetch content yourself, don't let user control URL
```

### Bypass Techniques
- **Redirects**: Allowlisted URL redirects to internal
- **DNS rebinding**: DNS resolves to internal IP after check
- **IP encoding**: `http://2130706433` = `127.0.0.1`
- **IPv6**: `http://[::1]`
- **URL parsing inconsistencies**: `http://trusted.com@evil.com`
- **Protocol smuggling**: `gopher://`, `file://`

### Cloud Metadata Endpoints to Block
```
# AWS
http://169.254.169.254/latest/meta-data/
http://[fd00:ec2::254]/latest/meta-data/

# GCP
http://metadata.google.internal/computeMetadata/v1/
http://169.254.169.254/computeMetadata/v1/

# Azure
http://169.254.169.254/metadata/instance
```

## Command Injection

### Defense: Avoid shells, use arrays
```python
# BAD
os.system(f"convert {filename} output.png")

# GOOD - no shell
subprocess.run(['convert', filename, 'output.png'], shell=False)

# BEST - validate filename
if not re.match(r'^[a-zA-Z0-9_.-]+$', filename):
    abort(400)
subprocess.run(['convert', filename, 'output.png'], shell=False)
```

### Bypass Techniques
- **Command chaining**: `; ls`, `&& ls`, `|| ls`
- **Subshells**: `$(ls)`, `` `ls` ``
- **Newlines**: `%0a` in URL
- **Null bytes**: Truncate string in some languages
