# Cloud Security

## SSRF to Cloud Metadata

### The Threat
SSRF vulnerabilities can access cloud metadata services to steal credentials.

### AWS IMDSv1 (VULNERABLE)
```
http://169.254.169.254/latest/meta-data/
http://169.254.169.254/latest/meta-data/iam/security-credentials/
http://169.254.169.254/latest/user-data
```

### AWS IMDSv2 (More Secure)
Requires token header — harder to exploit via SSRF:
```bash
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
curl -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/
```

### GCP Metadata
```
http://metadata.google.internal/computeMetadata/v1/
http://169.254.169.254/computeMetadata/v1/
```
Requires header: `Metadata-Flavor: Google`

### Azure Metadata
```
http://169.254.169.254/metadata/instance?api-version=2021-02-01
```
Requires header: `Metadata: true`

### Defense
```python
# Block metadata IPs in SSRF-prone code
BLOCKED_IPS = [
    '169.254.169.254',
    'fd00:ec2::254',
    '169.254.170.2',  # ECS task metadata
]

BLOCKED_HOSTS = [
    'metadata.google.internal',
    'metadata.goog',
]

def is_safe_url(url):
    parsed = urlparse(url)

    # Check hostname
    if parsed.hostname in BLOCKED_HOSTS:
        return False

    # Resolve and check IP
    try:
        ip = socket.gethostbyname(parsed.hostname)
        if ip in BLOCKED_IPS or ip.startswith('169.254.'):
            return False
    except socket.gaierror:
        return False

    return True
```

## IAM Security

### Principle of Least Privilege
```json
// BAD - overly permissive
{
    "Effect": "Allow",
    "Action": "s3:*",
    "Resource": "*"
}

// GOOD - specific permissions
{
    "Effect": "Allow",
    "Action": [
        "s3:GetObject",
        "s3:PutObject"
    ],
    "Resource": "arn:aws:s3:::my-bucket/uploads/*"
}
```

### IAM Checklist
- [ ] No wildcard (*) actions in production
- [ ] Resource-level permissions where possible
- [ ] Separate roles for different services
- [ ] Regular audit of unused permissions
- [ ] MFA on privileged accounts
- [ ] No long-lived access keys

## Secrets Management

### DON'T
```python
# Hardcoded in code
AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

# In environment variables (visible in process list)
os.environ['DB_PASSWORD']
```

### DO
```python
# AWS Secrets Manager
import boto3
client = boto3.client('secretsmanager')
secret = client.get_secret_value(SecretId='my-secret')

# HashiCorp Vault
import hvac
client = hvac.Client(url='https://vault.example.com')
secret = client.secrets.kv.read_secret_version(path='my-secret')
```

### Checklist
- [ ] No secrets in code or config files
- [ ] No secrets in environment variables for production
- [ ] Use secrets manager (AWS SM, Vault, etc.)
- [ ] Rotate secrets regularly
- [ ] Audit secret access
- [ ] Different secrets per environment

## S3 Bucket Security

### Common Misconfigurations
```python
# Check for public buckets
import boto3

s3 = boto3.client('s3')
acl = s3.get_bucket_acl(Bucket='my-bucket')

for grant in acl['Grants']:
    grantee = grant['Grantee']
    if grantee.get('URI') == 'http://acs.amazonaws.com/groups/global/AllUsers':
        print("DANGER: Bucket is public!")
```

### Secure Configuration
- [ ] Block public access at account level
- [ ] Enable versioning
- [ ] Enable server-side encryption
- [ ] Enable access logging
- [ ] Use bucket policies, not ACLs
- [ ] Enable MFA delete for sensitive data
