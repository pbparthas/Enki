# File Handling Security

## Path Traversal

### The Attack
User sends `../../etc/passwd` as filename.

### Defense
```python
import os

# BAD
def read_file(filename):
    return open(f"/uploads/{filename}").read()

# GOOD
def read_file(filename):
    # 1. Get basename only
    safe_name = os.path.basename(filename)

    # 2. Construct full path
    full_path = os.path.join('/uploads', safe_name)

    # 3. Verify still within jail
    if not full_path.startswith('/uploads/'):
        abort(400)

    # 4. Check exists
    if not os.path.isfile(full_path):
        abort(404)

    return open(full_path).read()
```

### Bypass Techniques
- **URL encoding**: `%2e%2e%2f` = `../`
- **Double encoding**: `%252e%252e%252f`
- **Unicode**: `..%c0%af` (overlong encoding)
- **Null byte**: `file.txt%00.jpg` (older systems)
- **Backslash on Windows**: `..\\..\\`

## File Upload Security

### Validation Checklist
```python
def validate_upload(file):
    # 1. Check file extension (whitelist)
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        abort(400, "File type not allowed")

    # 2. Check MIME type (can be spoofed, but adds layer)
    ALLOWED_MIMES = {'image/png', 'image/jpeg', 'image/gif', 'application/pdf'}
    if file.content_type not in ALLOWED_MIMES:
        abort(400, "Invalid content type")

    # 3. Check magic bytes
    header = file.read(8)
    file.seek(0)
    if not is_valid_magic(header, ext):
        abort(400, "File content doesn't match extension")

    # 4. Check file size
    MAX_SIZE = 10 * 1024 * 1024  # 10MB
    file.seek(0, 2)  # End
    size = file.tell()
    file.seek(0)
    if size > MAX_SIZE:
        abort(400, "File too large")

    # 5. Generate random filename
    safe_filename = f"{secrets.token_hex(16)}.{ext}"

    return safe_filename
```

### Magic Bytes Reference
```python
MAGIC_BYTES = {
    'png': b'\x89PNG',
    'jpg': b'\xff\xd8\xff',
    'gif': b'GIF8',
    'pdf': b'%PDF',
}
```

### Storage Best Practices
- [ ] Store outside web root
- [ ] Random filenames (not user-provided)
- [ ] Separate domain for serving uploads (avoid cookie leakage)
- [ ] Content-Disposition: attachment for downloads
- [ ] Scan with antivirus
- [ ] Strip metadata (EXIF) from images

### Dangerous File Types to Block
```python
DANGEROUS_EXTENSIONS = {
    'php', 'php3', 'php4', 'php5', 'phtml',  # PHP
    'asp', 'aspx', 'ashx', 'asmx',            # ASP.NET
    'jsp', 'jspx',                             # Java
    'exe', 'dll', 'bat', 'cmd', 'ps1',        # Windows
    'sh', 'bash',                              # Unix
    'svg',                                     # Can contain JS
    'html', 'htm', 'xhtml',                   # XSS vectors
    'swf',                                     # Flash
}
```

## Local File Inclusion (LFI)

### The Attack
```
GET /page?template=../../../../etc/passwd
```

### Defense
```python
# BAD
template = request.args.get('template')
return render_template(template)

# GOOD - whitelist
ALLOWED_TEMPLATES = {'home', 'about', 'contact'}
template = request.args.get('template')
if template not in ALLOWED_TEMPLATES:
    abort(404)
return render_template(f"{template}.html")
```
