# Access Control Vulnerabilities

## IDOR (Insecure Direct Object Reference)

### The Attack
User changes `?id=123` to `?id=124` and sees another user's data.

### Defense Pattern
```python
# ALWAYS check ownership/permission
def get_resource(resource_id):
    resource = Resource.query.get(resource_id)
    if not resource:
        abort(404)  # Don't reveal existence
    if not can_access(current_user, resource):
        abort(404)  # Same error - don't leak info
    return resource
```

### Bypass Techniques Attackers Use
- **Parameter pollution**: `?id=123&id=456` — some frameworks take last value
- **Type juggling**: `?id=123` vs `?id=123.0` vs `?id=0x7B`
- **Array injection**: `?id[]=123` in PHP
- **UUID guessing**: Short or predictable UUIDs
- **Encoded values**: Base64, hex encoding to bypass WAF
- **HTTP method switching**: GET blocked but POST works

### Checklist
- [ ] Every endpoint checks authorization, not just authentication
- [ ] Use unpredictable IDs (UUIDv4) not sequential integers
- [ ] Same error response for "not found" and "not authorized"
- [ ] Verify ownership through JOIN, not separate queries
- [ ] Test with two different user accounts

## Privilege Escalation

### Vertical (User → Admin)
```python
# BAD - role in client-controlled data
def update_user(request):
    user.role = request.json.get('role', user.role)  # Can set to 'admin'

# GOOD - whitelist allowed fields
ALLOWED_FIELDS = ['name', 'email', 'preferences']
def update_user(request):
    for field in ALLOWED_FIELDS:
        if field in request.json:
            setattr(user, field, request.json[field])
```

### Horizontal (User A → User B)
Same as IDOR — always verify ownership.

### Mass Assignment
```python
# BAD - accepts any field
User.query.filter_by(id=user_id).update(request.json)

# GOOD - explicit fields only
user.name = validated_data['name']
user.email = validated_data['email']
```

### Checklist
- [ ] Admin functions require admin role check
- [ ] Role/permission changes require elevated privileges
- [ ] Whitelist updateable fields, never blacklist
- [ ] Audit log all privilege changes
