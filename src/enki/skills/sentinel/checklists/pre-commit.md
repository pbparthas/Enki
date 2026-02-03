# Pre-Commit Security Checklist

Quick checks before every commit:

## Secrets
- [ ] No hardcoded API keys, passwords, tokens
- [ ] No AWS/GCP/Azure credentials
- [ ] No private keys or certificates
- [ ] `.env` files in `.gitignore`

## Input Handling
- [ ] All user input validated
- [ ] SQL uses parameterized queries
- [ ] Output properly encoded for context
- [ ] File paths use basename + jail

## Authentication
- [ ] Auth check on every protected endpoint
- [ ] Authorization (ownership) verified, not just authentication
- [ ] Sensitive operations require re-authentication

## Common Mistakes
- [ ] No `eval()`, `exec()` with user data
- [ ] No `dangerouslySetInnerHTML` with user content
- [ ] No `shell=True` in subprocess calls
- [ ] No disabled SSL verification
- [ ] No verbose error messages to users

## One-liner grep checks
```bash
# Hardcoded secrets
grep -rn "password\s*=\s*['\"]" --include="*.py" .
grep -rn "api_key\s*=\s*['\"]" --include="*.py" .
grep -rn "secret\s*=\s*['\"]" --include="*.py" .

# Dangerous patterns
grep -rn "eval(" --include="*.py" .
grep -rn "exec(" --include="*.py" .
grep -rn "shell=True" --include="*.py" .
grep -rn "verify=False" --include="*.py" .

# SQL injection
grep -rn "execute.*%s" --include="*.py" .
grep -rn "execute.*f\"" --include="*.py" .
grep -rn "execute.*f'" --include="*.py" .

# Command injection
grep -rn "os.system" --include="*.py" .
grep -rn "os.popen" --include="*.py" .

# XSS in React
grep -rn "dangerouslySetInnerHTML" --include="*.jsx" --include="*.tsx" .
```

## Git Hooks Integration

Add to `.git/hooks/pre-commit`:
```bash
#!/bin/bash

# Check for secrets
if grep -rn "password\s*=\s*['\"][^'\"]*['\"]" --include="*.py" .; then
    echo "ERROR: Possible hardcoded password found"
    exit 1
fi

if grep -rn "api_key\s*=\s*['\"][^'\"]*['\"]" --include="*.py" .; then
    echo "ERROR: Possible hardcoded API key found"
    exit 1
fi

# Check for dangerous patterns
if grep -rn "eval(" --include="*.py" .; then
    echo "WARNING: eval() found - ensure input is validated"
fi

exit 0
```
