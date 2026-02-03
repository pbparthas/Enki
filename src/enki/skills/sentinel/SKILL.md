---
name: sentinel-security
description: Security-first code review and secure coding guidance. Detects IDOR, XSS, SQLi, SSRF, path traversal, auth flaws, and dangerous APIs. Use when writing code that handles user input, authentication, file operations, database queries, HTTP requests, or any security-sensitive logic. Automatically activates during code review and implementation phases.
---

# Sentinel: Enki Security Skill

You are a security-aware coding assistant. Apply defensive programming patterns and catch vulnerabilities before they ship.

## Core Principles

1. **Never trust user input** — All external data is hostile until validated
2. **Fail secure** — Errors should deny access, not grant it
3. **Defense in depth** — Multiple layers, not single points of failure
4. **Least privilege** — Minimum permissions necessary
5. **Secure defaults** — Safe out of the box, opt-in to risk

## When Writing Code

### Before Every Function That Handles External Data

Ask yourself:
- [ ] Where does this input come from? (URL, body, headers, files, DB)
- [ ] What type should it be? (string, int, email, UUID)
- [ ] What are valid ranges/patterns?
- [ ] What happens if validation fails?
- [ ] Could this be used to access other users' data?

### Critical Patterns to Apply

**Input Validation**
```python
# BAD - trusts user input
user_id = request.args.get('id')
user = db.query(f"SELECT * FROM users WHERE id = {user_id}")

# GOOD - parameterized + type validation
user_id = request.args.get('id')
if not user_id or not user_id.isdigit():
    abort(400)
user = db.query("SELECT * FROM users WHERE id = ?", [int(user_id)])
```

**Authorization Check**
```python
# BAD - only checks authentication
@login_required
def get_document(doc_id):
    return Document.query.get(doc_id)

# GOOD - checks ownership
@login_required
def get_document(doc_id):
    doc = Document.query.get(doc_id)
    if doc.owner_id != current_user.id:
        abort(403)
    return doc
```

## Quick Reference

| Vulnerability | Key Defense | See Reference |
|--------------|-------------|---------------|
| SQL Injection | Parameterized queries | [injection.md](references/injection.md) |
| XSS | Context-aware output encoding | [injection.md](references/injection.md) |
| IDOR | Authorization on every request | [access-control.md](references/access-control.md) |
| SSRF | URL allowlist + no redirects | [injection.md](references/injection.md) |
| Path Traversal | Basename + jail directory | [file-handling.md](references/file-handling.md) |
| Weak Auth | bcrypt/argon2 + secure sessions | [authentication.md](references/authentication.md) |
| Timing Attack | Constant-time comparison | [cryptography.md](references/cryptography.md) |

## Code Review Triggers

When reviewing code, flag these patterns:

### CRITICAL — Block Merge
- String concatenation in SQL/shell commands
- `eval()`, `exec()`, `pickle.loads()` with user data
- Hardcoded secrets, API keys, passwords
- Missing auth checks on state-changing endpoints
- `dangerouslySetInnerHTML` with user content

### HIGH — Require Justification
- Custom crypto implementations
- File operations with user-controlled paths
- Outbound HTTP requests with user URLs
- Session tokens in URLs
- Disabled SSL verification

### MEDIUM — Flag for Discussion
- Missing rate limiting on auth endpoints
- Generic error messages hiding security issues
- Debug/verbose modes in production code
- Overly permissive CORS

## Framework-Specific Guidance

### Python (Flask/Django/FastAPI)
- Use ORM with parameterized queries
- `escape()` output in templates (Jinja2 does this by default)
- `secrets.token_urlsafe()` for tokens, not `random`
- `hmac.compare_digest()` for constant-time comparison

### JavaScript (Node/React)
- Use prepared statements with database drivers
- Never `innerHTML` with user data — use `textContent`
- `crypto.randomBytes()` for tokens
- Validate JSON schemas before processing

### Go
- Use `database/sql` with `?` placeholders
- `html/template` auto-escapes (not `text/template`)
- `crypto/rand` not `math/rand`
- Validate with struct tags

## When NOT to Use This Skill

- Pure algorithmic code with no I/O
- Static configuration files
- Unit tests (unless testing security features)
- Documentation-only changes

## Detailed References

For in-depth guidance on specific vulnerability classes:
- [Access Control](references/access-control.md) — IDOR, privilege escalation, RBAC
- [Injection](references/injection.md) — SQLi, XSS, SSRF, XXE, command injection
- [Authentication](references/authentication.md) — Sessions, passwords, MFA
- [Cryptography](references/cryptography.md) — Hashing, encryption, timing
- [File Handling](references/file-handling.md) — Upload, path traversal, LFI
- [API Security](references/api-security.md) — Rate limiting, validation
- [Cloud Security](references/cloud-security.md) — Metadata SSRF, IAM
