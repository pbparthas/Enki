# Security Code Review Checklist

Comprehensive checklist for security-focused code review.

## Authentication & Authorization

### Authentication
- [ ] Passwords hashed with bcrypt/argon2 (not MD5/SHA1)
- [ ] Session tokens are cryptographically random
- [ ] Sessions invalidated on logout
- [ ] Password reset tokens are time-limited
- [ ] Rate limiting on login attempts
- [ ] Account lockout after failed attempts

### Authorization
- [ ] Every endpoint checks authorization, not just authentication
- [ ] Resource ownership verified before access
- [ ] Admin functions require admin role check
- [ ] No privilege escalation via mass assignment
- [ ] API keys have minimal required permissions

## Input Validation

### General
- [ ] All user input validated and sanitized
- [ ] Whitelist validation preferred over blacklist
- [ ] Input length limits enforced
- [ ] Type validation (int, email, UUID, etc.)

### Database
- [ ] Parameterized queries used (no string concatenation)
- [ ] ORM used correctly (no raw queries with user input)
- [ ] Stored procedures use parameters

### Output
- [ ] HTML output encoded for context
- [ ] JSON responses don't leak sensitive data
- [ ] Error messages don't reveal internal details

## Injection Prevention

### SQL Injection
- [ ] No string formatting in SQL queries
- [ ] ORM methods used correctly
- [ ] Dynamic table/column names validated against whitelist

### XSS
- [ ] Template engine auto-escaping enabled
- [ ] No `dangerouslySetInnerHTML` with user data
- [ ] Content-Security-Policy header set
- [ ] User content served from separate domain

### Command Injection
- [ ] No `shell=True` with user input
- [ ] Arguments passed as arrays, not strings
- [ ] User input validated against strict patterns

### SSRF
- [ ] User-provided URLs validated against allowlist
- [ ] Redirects disabled or limited
- [ ] Cloud metadata IPs blocked
- [ ] Protocol restricted (http/https only)

## File Operations

### Path Traversal
- [ ] `os.path.basename()` used on user filenames
- [ ] Path resolved and verified within jail directory
- [ ] No user control over file extensions

### File Upload
- [ ] File type validated (extension, MIME, magic bytes)
- [ ] File size limited
- [ ] Random filenames generated
- [ ] Stored outside web root
- [ ] Dangerous extensions blocked

## Cryptography

### Random Numbers
- [ ] `secrets` module used (not `random`)
- [ ] Sufficient entropy (32+ bytes for tokens)

### Passwords
- [ ] bcrypt/argon2 with sufficient rounds
- [ ] No reversible encryption of passwords

### Encryption
- [ ] Established libraries used (no custom crypto)
- [ ] Keys managed securely (not hardcoded)
- [ ] IVs/nonces not reused
- [ ] Authenticated encryption (GCM) preferred

## Session Management

- [ ] Secure cookie flags set (Secure, HttpOnly, SameSite)
- [ ] Session ID regenerated after login
- [ ] Session timeout configured
- [ ] Concurrent session limits (if required)

## Error Handling

- [ ] Detailed errors logged server-side
- [ ] Generic errors returned to users
- [ ] No stack traces in production responses
- [ ] Fail-secure (errors deny access, not grant)

## Logging & Monitoring

- [ ] Security events logged (login, logout, failures)
- [ ] Sensitive data not in logs (passwords, tokens)
- [ ] Log injection prevented
- [ ] Audit trail for privilege changes

## Dependencies

- [ ] Dependencies up to date
- [ ] Known vulnerabilities checked (npm audit, safety)
- [ ] Minimal dependency footprint
- [ ] Lock files committed
