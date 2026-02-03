# Pre-Deployment Security Checklist

Final security checks before deploying to production.

## Configuration

### Environment
- [ ] Debug mode disabled
- [ ] Verbose errors disabled
- [ ] Production database credentials
- [ ] No test/development endpoints exposed
- [ ] Environment variables properly set

### Secrets
- [ ] No hardcoded secrets in code
- [ ] Secrets in secrets manager (not env vars)
- [ ] Different secrets per environment
- [ ] API keys have minimal permissions
- [ ] Old/unused secrets rotated

## Network Security

### HTTPS
- [ ] TLS 1.2+ enforced
- [ ] Valid SSL certificate
- [ ] HSTS header enabled
- [ ] HTTP redirects to HTTPS

### Headers
- [ ] `X-Content-Type-Options: nosniff`
- [ ] `X-Frame-Options: DENY`
- [ ] `X-XSS-Protection: 1; mode=block`
- [ ] `Strict-Transport-Security` configured
- [ ] `Content-Security-Policy` configured

### CORS
- [ ] Origins explicitly whitelisted
- [ ] No wildcard (`*`) in production
- [ ] Credentials properly handled

## Authentication

### Sessions
- [ ] Secure cookie flags enabled
- [ ] Appropriate session timeout
- [ ] Session invalidation working

### Passwords
- [ ] Strong hashing (bcrypt/argon2)
- [ ] Password requirements enforced
- [ ] Rate limiting on login

### MFA
- [ ] MFA available for users
- [ ] MFA required for admin accounts
- [ ] Backup codes generated

## Database

### Access
- [ ] Application uses limited-privilege account
- [ ] No admin credentials in app
- [ ] Connection encrypted (SSL)
- [ ] Firewall rules restrict access

### Data
- [ ] Sensitive data encrypted at rest
- [ ] PII handling compliant
- [ ] Backup encryption enabled
- [ ] Retention policies configured

## Logging & Monitoring

### Logging
- [ ] Security events logged
- [ ] No sensitive data in logs
- [ ] Log retention configured
- [ ] Logs shipped to central system

### Monitoring
- [ ] Error alerting configured
- [ ] Security event alerting
- [ ] Resource monitoring (CPU, memory, disk)
- [ ] Uptime monitoring

### Incident Response
- [ ] Runbook documented
- [ ] Contact list updated
- [ ] Rollback procedure tested

## Infrastructure

### Servers
- [ ] OS patches applied
- [ ] Unnecessary services disabled
- [ ] Firewall rules minimal
- [ ] SSH key authentication only

### Cloud (if applicable)
- [ ] IAM roles follow least privilege
- [ ] No public S3 buckets
- [ ] VPC configured correctly
- [ ] Security groups minimal
- [ ] CloudTrail/audit logging enabled

### Containers (if applicable)
- [ ] Base images up to date
- [ ] No secrets in images
- [ ] Non-root user
- [ ] Read-only filesystem where possible
- [ ] Resource limits set

## Dependencies

- [ ] All dependencies updated
- [ ] Vulnerability scan passed
- [ ] License compliance checked
- [ ] Lock files committed

## Testing

- [ ] Security tests passed
- [ ] Penetration test (if required)
- [ ] OWASP Top 10 covered
- [ ] Authentication flows tested
- [ ] Authorization tested with multiple roles

## Documentation

- [ ] Security architecture documented
- [ ] API security documented
- [ ] Incident response plan
- [ ] Data flow diagrams current
