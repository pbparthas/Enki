"""Enki API Client - connects to remote Enki server.

Supports:
- JWT authentication with auto-refresh
- Offline mode with local caching and sync queue
- Automatic reconnection and sync

When ENKI_API_URL and ENKI_API_KEY are set, this client is used
instead of local database operations. Embeddings are computed
locally and sent to the server.
"""

import json
import os
import threading
import time
from typing import Optional
import urllib.request
import urllib.error

from .offline import (
    ConnectionState,
    get_connection_state,
    set_connection_state,
    is_online,
    is_offline,
    cache_bead,
    get_cached_bead,
    search_cached_beads,
    queue_operation,
    get_pending_operations,
    mark_operation_syncing,
    mark_operation_complete,
    mark_operation_failed,
    get_queue_size,
    get_sync_status,
    should_retry,
    update_last_sync,
    SyncStatus,
)
from .auth import (
    TokenPair,
    store_tokens,
    get_stored_tokens,
    clear_stored_tokens,
    needs_refresh,
    is_refresh_token_valid,
    get_access_token,
)

# Check for remote mode
ENKI_API_URL = os.environ.get("ENKI_API_URL", "").rstrip("/")
ENKI_API_KEY = os.environ.get("ENKI_API_KEY", "")

# Lazy-load embedding model only when needed
_embedding_model = None

# Lock for token refresh
_token_refresh_lock = threading.Lock()

# Background sync thread
_sync_thread: Optional[threading.Thread] = None
_sync_stop_event = threading.Event()

# Sync interval (1 hour)
SYNC_INTERVAL_SECONDS = 3600


def is_remote_mode() -> bool:
    """Check if running in remote mode."""
    return bool(ENKI_API_URL and ENKI_API_KEY)


def get_embedding_model():
    """Get the embedding model, loading it lazily."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model


def compute_embedding(text: str) -> list[float]:
    """Compute embedding for text using local model."""
    model = get_embedding_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


# --- Connectivity ---

def _check_connectivity(timeout: float = 5.0) -> bool:
    """Test if the Enki server is reachable.

    Args:
        timeout: Connection timeout in seconds

    Returns:
        True if server is reachable, False otherwise
    """
    if not ENKI_API_URL:
        return False

    try:
        url = f"{ENKI_API_URL}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _handle_online() -> None:
    """Handle transition to online state."""
    prev_state = get_connection_state()
    set_connection_state(ConnectionState.ONLINE)

    if prev_state == ConnectionState.OFFLINE:
        # Trigger sync on reconnection
        _sync_pending_operations()


def _handle_offline() -> None:
    """Handle transition to offline state."""
    set_connection_state(ConnectionState.OFFLINE)


def check_and_update_connectivity() -> bool:
    """Check connectivity and update state.

    Returns:
        True if online, False if offline
    """
    if _check_connectivity():
        _handle_online()
        return True
    else:
        _handle_offline()
        return False


# --- JWT Auth ---

def _get_auth_header() -> dict:
    """Get authorization header, refreshing token if needed.

    Returns:
        Dict with Authorization header, or empty dict
    """
    # Try JWT first
    token = get_access_token()
    if token:
        # Check if we need to refresh
        if needs_refresh():
            new_token = _refresh_tokens()
            if new_token:
                token = new_token

        return {"Authorization": f"Bearer {token}"}

    # Fall back to API key
    if ENKI_API_KEY:
        return {"Authorization": f"Bearer {ENKI_API_KEY}"}

    return {}


def _refresh_tokens() -> Optional[str]:
    """Refresh JWT tokens using refresh token.

    Returns:
        New access token if successful, None otherwise
    """
    with _token_refresh_lock:
        # Double-check after acquiring lock
        if not needs_refresh():
            return get_access_token()

        tokens = get_stored_tokens()
        if not tokens or not is_refresh_token_valid():
            return None

        try:
            url = f"{ENKI_API_URL}/auth/refresh"
            data = json.dumps({"refresh_token": tokens.refresh_token}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))

                from datetime import datetime, timezone
                new_tokens = TokenPair(
                    access_token=result["access_token"],
                    refresh_token=result.get("refresh_token", tokens.refresh_token),
                    access_expires=datetime.fromisoformat(result["access_expires"]),
                    refresh_expires=datetime.fromisoformat(result.get("refresh_expires", tokens.refresh_expires.isoformat())),
                )
                store_tokens(new_tokens)
                return new_tokens.access_token

        except Exception:
            return None


def login(api_key: Optional[str] = None) -> bool:
    """Login and exchange API key for JWT tokens.

    Args:
        api_key: API key to use (defaults to ENKI_API_KEY env var)

    Returns:
        True if login successful, False otherwise
    """
    key = api_key or ENKI_API_KEY
    if not key or not ENKI_API_URL:
        return False

    try:
        url = f"{ENKI_API_URL}/auth/login"
        data = json.dumps({"api_key": key}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

            from datetime import datetime
            tokens = TokenPair(
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                access_expires=datetime.fromisoformat(result["access_expires"]),
                refresh_expires=datetime.fromisoformat(result["refresh_expires"]),
            )
            store_tokens(tokens)
            _handle_online()
            return True

    except Exception:
        return False


def logout() -> None:
    """Logout and clear stored tokens."""
    clear_stored_tokens()


# --- API Calls ---

def _api_call(
    endpoint: str,
    method: str = "GET",
    data: Optional[dict] = None,
    allow_offline: bool = True,
) -> dict:
    """Make an API call to the Enki server.

    Args:
        endpoint: API endpoint
        method: HTTP method
        data: Request body data
        allow_offline: If True, raises ConnectionError when offline

    Returns:
        Response data as dict

    Raises:
        ConnectionError: If offline and allow_offline is True
        Exception: For other API errors
    """
    url = f"{ENKI_API_URL}/{endpoint.lstrip('/')}"

    headers = {
        "Content-Type": "application/json",
        **_get_auth_header(),
    }

    body = json.dumps(data).encode("utf-8") if data else None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            _handle_online()
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_data = json.loads(error_body)
            raise Exception(f"API Error: {error_data.get('detail', error_body)}")
        except json.JSONDecodeError:
            raise Exception(f"API Error ({e.code}): {error_body}")
    except urllib.error.URLError as e:
        if allow_offline:
            _handle_offline()
            raise ConnectionError(f"Connection Error: {e.reason}")
        raise Exception(f"Connection Error: {e.reason}")


# --- Remote Operations ---

def remote_remember(
    content: str,
    bead_type: str,
    summary: Optional[str] = None,
    project: Optional[str] = None,
    context: Optional[str] = None,
    tags: Optional[list[str]] = None,
    starred: bool = False,
) -> dict:
    """Store a bead on the remote server with embedding.

    If offline, queues the operation and caches locally.
    """
    # Compute embedding locally
    text_for_embedding = f"{summary or ''} {content}"
    embedding = compute_embedding(text_for_embedding)

    data = {
        "content": content,
        "type": bead_type,
        "summary": summary,
        "project": project,
        "context": context,
        "tags": tags,
        "starred": starred,
        "embedding": embedding,
    }

    try:
        result = _api_call("remember", "POST", data)

        # Cache the result locally
        cache_bead(
            bead_id=result["id"],
            content=content,
            bead_type=bead_type,
            summary=summary,
            project=project,
            weight=1.0,
            starred=starred,
            tags=tags,
            embedding=embedding,
        )

        return result

    except ConnectionError:
        # Queue for later sync
        queue_operation("remember", data)

        # Generate temporary local ID
        import uuid
        temp_id = f"offline-{uuid.uuid4().hex[:8]}"

        # Cache locally
        cache_bead(
            bead_id=temp_id,
            content=content,
            bead_type=bead_type,
            summary=summary,
            project=project,
            weight=1.0,
            starred=starred,
            tags=tags,
            embedding=embedding,
        )

        return {"id": temp_id, "status": "queued", "offline": True}


def remote_recall(
    query: str,
    project: Optional[str] = None,
    bead_type: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Search beads on the remote server using semantic search.

    If offline, searches local cache.
    """
    # Compute query embedding locally
    query_embedding = compute_embedding(query)

    try:
        data = {
            "query": query,
            "query_embedding": query_embedding,
            "project": project,
            "type": bead_type,
            "limit": limit,
        }

        result = _api_call("recall", "POST", data)
        results = result.get("results", [])

        # Cache results locally
        for r in results:
            cache_bead(
                bead_id=r["id"],
                content=r.get("content", ""),
                bead_type=r.get("type", "learning"),
                summary=r.get("summary"),
                project=r.get("project"),
                weight=r.get("weight", 1.0),
                starred=False,
            )

        return results

    except ConnectionError:
        # Search local cache
        cached_results = search_cached_beads(
            query_embedding=query_embedding,
            project=project,
            bead_type=bead_type,
            limit=limit,
        )

        return [
            {
                "id": bead.id,
                "content": bead.content,
                "type": bead.type,
                "summary": bead.summary,
                "project": bead.project,
                "weight": bead.weight,
                "score": score,
                "cached": True,
            }
            for bead, score in cached_results
        ]


def remote_star(bead_id: str, starred: bool = True) -> dict:
    """Star or unstar a bead on the remote server.

    If offline, queues the operation.
    """
    data = {"bead_id": bead_id, "starred": starred}

    try:
        return _api_call("star", "POST", data)
    except ConnectionError:
        queue_operation("star", data)
        return {"status": "queued", "bead_id": bead_id, "starred": starred, "offline": True}


def remote_supersede(old_id: str, new_id: str) -> dict:
    """Mark a bead as superseded on the remote server.

    If offline, queues the operation.
    """
    data = {"old_id": old_id, "new_id": new_id}

    try:
        return _api_call("supersede", "POST", data)
    except ConnectionError:
        queue_operation("supersede", data)
        return {"status": "queued", "old_id": old_id, "new_id": new_id, "offline": True}


def remote_status(project: Optional[str] = None) -> dict:
    """Get status from the remote server.

    If offline, returns cached status.
    """
    try:
        params = f"?project={project}" if project else ""
        return _api_call(f"status{params}", "GET")
    except ConnectionError:
        # Return offline status
        from .offline import get_cache_count, get_queue_size
        return {
            "phase": "unknown",
            "goal": None,
            "total_beads": 0,
            "active_beads": 0,
            "starred_beads": 0,
            "offline": True,
            "cached_beads": get_cache_count(),
            "pending_sync": get_queue_size(),
        }


def remote_goal(goal: str, project: Optional[str] = None) -> dict:
    """Set goal on the remote server.

    If offline, queues the operation.
    """
    data = {"goal": goal, "project": project}

    try:
        return _api_call("goal", "POST", data)
    except ConnectionError:
        queue_operation("goal", data)
        return {"status": "queued", "goal": goal, "offline": True}


def remote_phase(phase: Optional[str] = None, project: Optional[str] = None) -> dict:
    """Get or set phase on the remote server.

    If offline, queues set operations.
    """
    data = {"phase": phase, "project": project}

    try:
        return _api_call("phase", "POST", data)
    except ConnectionError:
        if phase:
            queue_operation("phase", data)
            return {"phase": phase, "status": "queued", "offline": True}
        return {"phase": "unknown", "offline": True}


def remote_get_bead(bead_id: str) -> dict:
    """Get a specific bead from the remote server.

    If offline, returns from cache.
    """
    try:
        return _api_call(f"bead/{bead_id}", "GET")
    except ConnectionError:
        cached = get_cached_bead(bead_id)
        if cached:
            return {
                "id": cached.id,
                "content": cached.content,
                "type": cached.type,
                "summary": cached.summary,
                "project": cached.project,
                "weight": cached.weight,
                "starred": cached.starred,
                "cached": True,
            }
        raise Exception(f"Bead {bead_id} not found in cache")


# --- Sync Operations ---

def _sync_pending_operations() -> dict:
    """Drain the sync queue and send pending operations to server.

    Returns:
        Dict with sync results
    """
    if not is_remote_mode():
        return {"synced": 0, "failed": 0, "skipped": 0}

    set_connection_state(ConnectionState.SYNCING)

    synced = 0
    failed = 0
    skipped = 0

    pending = get_pending_operations()

    for op in pending:
        if not should_retry(op):
            skipped += 1
            continue

        mark_operation_syncing(op.id)

        try:
            if op.operation == "remember":
                _api_call("remember", "POST", op.payload, allow_offline=False)
            elif op.operation == "star":
                _api_call("star", "POST", op.payload, allow_offline=False)
            elif op.operation == "supersede":
                _api_call("supersede", "POST", op.payload, allow_offline=False)
            elif op.operation == "goal":
                _api_call("goal", "POST", op.payload, allow_offline=False)
            elif op.operation == "phase":
                _api_call("phase", "POST", op.payload, allow_offline=False)

            mark_operation_complete(op.id)
            synced += 1

        except Exception:
            mark_operation_failed(op.id)
            failed += 1

    if _check_connectivity():
        set_connection_state(ConnectionState.ONLINE)
    else:
        set_connection_state(ConnectionState.OFFLINE)

    update_last_sync()

    return {"synced": synced, "failed": failed, "skipped": skipped}


def force_sync() -> dict:
    """Force an immediate sync of pending operations.

    Returns:
        Dict with sync results
    """
    if not _check_connectivity():
        return {"error": "Server not reachable", "offline": True}

    return _sync_pending_operations()


def client_get_sync_status() -> SyncStatus:
    """Get current sync status.

    Returns:
        SyncStatus object
    """
    return get_sync_status()


# --- Background Sync Daemon ---

def _sync_daemon_loop() -> None:
    """Background sync loop."""
    while not _sync_stop_event.is_set():
        _sync_stop_event.wait(SYNC_INTERVAL_SECONDS)

        if _sync_stop_event.is_set():
            break

        if _check_connectivity():
            if get_queue_size() > 0:
                _sync_pending_operations()


def start_sync_daemon() -> None:
    """Start background sync daemon thread."""
    global _sync_thread

    if _sync_thread and _sync_thread.is_alive():
        return

    _sync_stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_daemon_loop, daemon=True)
    _sync_thread.start()


def stop_sync_daemon() -> None:
    """Stop background sync daemon."""
    _sync_stop_event.set()


# --- Startup ---

def startup_sync() -> dict:
    """Perform startup sync - check connectivity and drain queue.

    Called when MCP server starts.

    Returns:
        Dict with startup sync results
    """
    if not is_remote_mode():
        return {"mode": "local"}

    if not _check_connectivity():
        set_connection_state(ConnectionState.OFFLINE)
        return {"mode": "offline", "pending": get_queue_size()}

    # Online - drain any pending operations
    result = _sync_pending_operations()
    start_sync_daemon()

    return {
        "mode": "online",
        **result,
    }
