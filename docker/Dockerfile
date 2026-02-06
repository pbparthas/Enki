# Enki Lightweight Server Dockerfile
# No ML dependencies - clients compute embeddings
# P3-21: Pin base image to specific version for reproducibility
FROM python:3.11.11-slim-bookworm

WORKDIR /app

# Create package structure
RUN mkdir -p src/enki

# Copy server files (minimal - no ML dependencies needed)
COPY src/enki/api_server.py src/enki/api_server.py
COPY src/enki/db.py src/enki/db.py
COPY src/enki/auth.py src/enki/auth.py

# Create minimal __init__.py for server (avoids importing ML modules)
RUN echo '"""Enki server package."""' > src/enki/__init__.py

# Install minimal dependencies (no sentence-transformers)
RUN pip install --no-cache-dir \
    fastapi>=0.109.0 \
    uvicorn[standard]>=0.27.0 \
    numpy>=1.24.0 \
    pyjwt>=2.8.0

# Create data directory
RUN mkdir -p /data

# Set environment
ENV ENKI_DB_PATH=/data/wisdom.db
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# P1-16: Run as non-root user
RUN useradd -m enki && chown -R enki:enki /data
USER enki

# Expose port
EXPOSE 8002

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')" || exit 1

# Run server
CMD ["python", "-m", "uvicorn", "enki.api_server:app", "--host", "0.0.0.0", "--port", "8002"]
