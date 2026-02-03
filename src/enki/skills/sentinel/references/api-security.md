# API Security

## Rate Limiting

### Required Endpoints
- Login: 5 requests / 15 minutes per IP
- Password reset: 3 requests / hour per email
- API endpoints: 100 requests / minute per user
- Public endpoints: 20 requests / minute per IP

### Implementation
```python
from functools import wraps
from flask import request
import time

# Simple in-memory (use Redis in production)
rate_limits = {}

def rate_limit(max_requests, window_seconds):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            key = f"{request.remote_addr}:{f.__name__}"
            now = time.time()

            if key not in rate_limits:
                rate_limits[key] = []

            # Remove old entries
            rate_limits[key] = [t for t in rate_limits[key]
                               if now - t < window_seconds]

            if len(rate_limits[key]) >= max_requests:
                abort(429, "Too many requests")

            rate_limits[key].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

@app.route('/login', methods=['POST'])
@rate_limit(5, 900)  # 5 per 15 min
def login():
    ...
```

## Input Validation

### JSON Schema Validation
```python
from jsonschema import validate, ValidationError

USER_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 100},
        "email": {"type": "string", "format": "email"},
        "age": {"type": "integer", "minimum": 0, "maximum": 150}
    },
    "required": ["name", "email"],
    "additionalProperties": False  # IMPORTANT: reject unknown fields
}

@app.route('/user', methods=['POST'])
def create_user():
    try:
        validate(request.json, USER_SCHEMA)
    except ValidationError as e:
        abort(400, str(e.message))
    ...
```

## API Response Security

### Don't Leak Data
```python
# BAD - leaks internal info
return {"error": str(exception), "stack": traceback.format_exc()}

# GOOD - generic message, log details server-side
app.logger.error(f"Error: {exception}", exc_info=True)
return {"error": "An error occurred"}, 500
```

### Pagination Limits
```python
# Always limit results
MAX_PAGE_SIZE = 100

def get_items():
    limit = min(request.args.get('limit', 20, type=int), MAX_PAGE_SIZE)
    offset = request.args.get('offset', 0, type=int)
    return Item.query.limit(limit).offset(offset).all()
```

## Security Headers

```python
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
    return response
```

## CORS Configuration

```python
# BAD - allows any origin
@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

# GOOD - specific origins
ALLOWED_ORIGINS = ['https://app.example.com', 'https://admin.example.com']

@app.after_request
def add_cors(response):
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response
```
