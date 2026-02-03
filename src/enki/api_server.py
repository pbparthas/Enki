"""Enki REST API Server for cross-machine memory sync.

Lightweight server that stores beads and embeddings.
Clients compute embeddings, server stores and searches.
No ML dependencies required on server.

Supports:
- API key authentication (legacy)
- JWT token authentication (preferred)
"""

import json
import os
import secrets
import struct
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import numpy as np
import jwt

from .db import init_db, get_db

# API Key from environment
API_KEY = os.environ.get("ENKI_API_KEY", "")

# JWT configuration
JWT_SECRET = os.environ.get("ENKI_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30

app = FastAPI(
    title="Enki API",
    description="Second brain memory API for cross-machine sync",
    version="0.1.0",
)


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    init_db()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth ---

def _create_access_token(user_id: str) -> tuple[str, datetime]:
    """Create a JWT access token."""
    if not JWT_SECRET:
        raise HTTPException(500, "ENKI_JWT_SECRET not configured on server")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": user_id,
        "type": "access",
        "exp": expires,
        "iat": now,
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, expires


def _create_refresh_token(user_id: str) -> tuple[str, datetime]:
    """Create a JWT refresh token."""
    if not JWT_SECRET:
        raise HTTPException(500, "ENKI_JWT_SECRET not configured on server")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expires,
        "iat": now,
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, expires


def _verify_jwt_token(token: str, expected_type: str = "access") -> Optional[dict]:
    """Verify a JWT token and return its payload.

    Returns None if token is invalid or wrong type.
    """
    if not JWT_SECRET:
        return None

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


async def verify_api_key(
    authorization: Annotated[str | None, Header()] = None,
    key: Annotated[str | None, Query()] = None,
) -> str:
    """Verify API key or JWT from header or query param.

    Supports both:
    - Legacy API key authentication
    - JWT token authentication
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif key:
        token = key

    if not token:
        raise HTTPException(401, "Missing authentication")

    # Try JWT first (if JWT_SECRET is configured)
    if JWT_SECRET:
        payload = _verify_jwt_token(token, "access")
        if payload:
            return payload.get("sub", "jwt-user")

    # Fall back to API key
    if not API_KEY:
        raise HTTPException(500, "Authentication not configured on server")

    if secrets.compare_digest(token, API_KEY):
        return "api-key-user"

    raise HTTPException(403, "Invalid authentication")


# --- Vector utilities ---

def vector_to_bytes(vector: list[float]) -> bytes:
    """Convert float list to bytes for storage."""
    return struct.pack(f'{len(vector)}f', *vector)


def bytes_to_vector(data: bytes) -> np.ndarray:
    """Convert bytes back to numpy array."""
    return np.array(struct.unpack(f'{len(data)//4}f', data))


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# --- Models ---

class RememberRequest(BaseModel):
    content: str
    type: str = Field(..., pattern="^(decision|solution|learning|violation|pattern)$")
    summary: Optional[str] = None
    project: Optional[str] = None
    context: Optional[str] = None
    tags: Optional[list[str]] = None
    starred: bool = False
    embedding: Optional[list[float]] = None  # Client-computed embedding


class RecallRequest(BaseModel):
    query: str
    query_embedding: Optional[list[float]] = None  # Client-computed query embedding
    project: Optional[str] = None
    type: Optional[str] = None
    limit: int = 10


class StarRequest(BaseModel):
    bead_id: str
    starred: bool = True


class SupersedeRequest(BaseModel):
    old_id: str
    new_id: str


class GoalRequest(BaseModel):
    goal: str
    project: Optional[str] = None


class PhaseRequest(BaseModel):
    phase: Optional[str] = None
    project: Optional[str] = None


class StatusResponse(BaseModel):
    phase: str
    goal: Optional[str]
    total_beads: int
    active_beads: int
    starred_beads: int


# --- Auth Models ---

class LoginRequest(BaseModel):
    api_key: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    access_expires: str
    refresh_expires: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    access_expires: str
    token_type: str = "bearer"


# --- Database helpers ---

def create_bead_with_embedding(
    content: str,
    bead_type: str,
    summary: Optional[str] = None,
    project: Optional[str] = None,
    context: Optional[str] = None,
    tags: Optional[list[str]] = None,
    starred: bool = False,
    embedding: Optional[list[float]] = None,
) -> str:
    """Create a bead and optionally store its embedding."""
    conn = get_db()
    bead_id = str(uuid.uuid4())[:8]

    conn.execute(
        """
        INSERT INTO beads (id, content, summary, type, project, context, tags, starred, weight)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0)
        """,
        (
            bead_id,
            content,
            summary,
            bead_type,
            project,
            context,
            json.dumps(tags) if tags else None,
            1 if starred else 0,
        ),
    )

    # Store embedding if provided
    if embedding:
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (bead_id, vector, model) VALUES (?, ?, ?)",
            (bead_id, vector_to_bytes(embedding), "client-provided"),
        )

    conn.commit()
    return bead_id


def search_beads(
    query: str,
    query_embedding: Optional[list[float]] = None,
    project: Optional[str] = None,
    bead_type: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Search beads using FTS and/or vector similarity."""
    conn = get_db()

    # If we have a query embedding, do vector search
    if query_embedding:
        query_vec = np.array(query_embedding)

        # Get all beads with embeddings
        sql = """
            SELECT b.*, e.vector
            FROM beads b
            JOIN embeddings e ON b.id = e.bead_id
            WHERE b.superseded_by IS NULL
        """
        params = []

        if project:
            sql += " AND b.project = ?"
            params.append(project)
        if bead_type:
            sql += " AND b.type = ?"
            params.append(bead_type)

        rows = conn.execute(sql, params).fetchall()

        # Compute similarities
        results = []
        for row in rows:
            vec = bytes_to_vector(row["vector"])
            score = cosine_similarity(query_vec, vec)
            results.append({
                "id": row["id"],
                "content": row["content"],
                "type": row["type"],
                "summary": row["summary"],
                "project": row["project"],
                "weight": row["weight"],
                "score": score,
            })

        # Sort by score and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # Fall back to FTS search
    sql = """
        SELECT b.*, bfs.rank as score
        FROM beads b
        JOIN beads_fts bfs ON b.rowid = bfs.rowid
        WHERE beads_fts MATCH ? AND b.superseded_by IS NULL
    """
    params = [query]

    if project:
        sql += " AND b.project = ?"
        params.append(project)
    if bead_type:
        sql += " AND b.type = ?"
        params.append(bead_type)

    sql += " ORDER BY bfs.rank LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"],
                "content": row["content"],
                "type": row["type"],
                "summary": row["summary"],
                "project": row["project"],
                "weight": row["weight"],
                "score": abs(row["score"]) if row["score"] else 0,
            }
            for row in rows
        ]
    except Exception:
        # FTS query syntax error - return empty
        return []


def get_bead_by_id(bead_id: str) -> Optional[dict]:
    """Get a bead by ID."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM beads WHERE id = ?", (bead_id,)
    ).fetchone()
    if row:
        return dict(row)
    return None


def star_bead(bead_id: str) -> None:
    """Star a bead."""
    conn = get_db()
    conn.execute("UPDATE beads SET starred = 1 WHERE id = ?", (bead_id,))
    conn.commit()


def unstar_bead(bead_id: str) -> None:
    """Unstar a bead."""
    conn = get_db()
    conn.execute("UPDATE beads SET starred = 0 WHERE id = ?", (bead_id,))
    conn.commit()


def supersede_bead(old_id: str, new_id: str) -> None:
    """Mark a bead as superseded."""
    conn = get_db()
    conn.execute("UPDATE beads SET superseded_by = ? WHERE id = ?", (new_id, old_id))
    conn.commit()


# --- Session helpers ---

_session_state: dict = {"phase": "intake", "goal": None}


def get_phase(project: Optional[str] = None) -> str:
    """Get current phase."""
    return _session_state.get("phase", "intake")


def set_phase(phase: str, project: Optional[str] = None) -> None:
    """Set current phase."""
    _session_state["phase"] = phase


def get_goal(project: Optional[str] = None) -> Optional[str]:
    """Get current goal."""
    return _session_state.get("goal")


def set_goal(goal: str, project: Optional[str] = None) -> None:
    """Set current goal."""
    _session_state["goal"] = goal


# --- Endpoints ---

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "enki", "mode": "lightweight"}


# --- Auth Endpoints ---

@app.post("/auth/login", response_model=LoginResponse)
async def auth_login(req: LoginRequest):
    """Exchange API key for JWT tokens.

    This endpoint allows clients to exchange their API key for
    JWT access and refresh tokens, enabling token-based auth.
    """
    if not API_KEY:
        raise HTTPException(500, "ENKI_API_KEY not configured on server")

    if not JWT_SECRET:
        raise HTTPException(500, "ENKI_JWT_SECRET not configured on server")

    # Verify API key
    if not secrets.compare_digest(req.api_key, API_KEY):
        raise HTTPException(403, "Invalid API key")

    # Generate user ID (could be enhanced with actual user management)
    user_id = f"enki-{uuid.uuid4().hex[:8]}"

    # Create tokens
    access_token, access_expires = _create_access_token(user_id)
    refresh_token, refresh_expires = _create_refresh_token(user_id)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires=access_expires.isoformat(),
        refresh_expires=refresh_expires.isoformat(),
    )


@app.post("/auth/refresh", response_model=RefreshResponse)
async def auth_refresh(req: RefreshRequest):
    """Refresh an access token using a refresh token.

    Returns a new access token. The refresh token remains valid
    until its expiry date.
    """
    if not JWT_SECRET:
        raise HTTPException(500, "ENKI_JWT_SECRET not configured on server")

    # Verify refresh token
    payload = _verify_jwt_token(req.refresh_token, "refresh")
    if not payload:
        raise HTTPException(401, "Invalid or expired refresh token")

    user_id = payload.get("sub", "unknown")

    # Create new access token
    access_token, access_expires = _create_access_token(user_id)

    return RefreshResponse(
        access_token=access_token,
        access_expires=access_expires.isoformat(),
    )


@app.post("/remember")
async def api_remember(
    req: RememberRequest,
    _: str = Depends(verify_api_key),
):
    """Store a new piece of knowledge with optional embedding."""
    try:
        bead_id = create_bead_with_embedding(
            content=req.content,
            bead_type=req.type,
            summary=req.summary,
            project=req.project,
            context=req.context,
            tags=req.tags,
            starred=req.starred,
            embedding=req.embedding,
        )
        return {"id": bead_id, "status": "remembered"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/recall")
async def api_recall(
    req: RecallRequest,
    _: str = Depends(verify_api_key),
):
    """Search for relevant knowledge using FTS or vector similarity."""
    try:
        results = search_beads(
            query=req.query,
            query_embedding=req.query_embedding,
            project=req.project,
            bead_type=req.type,
            limit=req.limit,
        )
        return {"results": results}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/star")
async def api_star(
    req: StarRequest,
    _: str = Depends(verify_api_key),
):
    """Star or unstar a bead."""
    try:
        if req.starred:
            star_bead(req.bead_id)
        else:
            unstar_bead(req.bead_id)
        return {"status": "ok", "bead_id": req.bead_id, "starred": req.starred}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/supersede")
async def api_supersede(
    req: SupersedeRequest,
    _: str = Depends(verify_api_key),
):
    """Mark a bead as superseded by another."""
    try:
        supersede_bead(req.old_id, req.new_id)
        return {"status": "ok", "old_id": req.old_id, "new_id": req.new_id}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/status")
async def api_status(
    project: Optional[str] = None,
    _: str = Depends(verify_api_key),
):
    """Get memory statistics and session status."""
    try:
        conn = get_db()

        phase = get_phase(project)
        goal = get_goal(project)

        total = conn.execute("SELECT COUNT(*) FROM beads").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE superseded_by IS NULL"
        ).fetchone()[0]
        starred = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE starred = 1"
        ).fetchone()[0]

        return StatusResponse(
            phase=phase or "intake",
            goal=goal,
            total_beads=total,
            active_beads=active,
            starred_beads=starred,
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/goal")
async def api_goal(
    req: GoalRequest,
    _: str = Depends(verify_api_key),
):
    """Set the session goal."""
    try:
        set_goal(req.goal, req.project)
        return {"status": "ok", "goal": req.goal}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/phase")
async def api_phase(
    req: PhaseRequest,
    _: str = Depends(verify_api_key),
):
    """Get or set the current phase."""
    try:
        if req.phase:
            set_phase(req.phase, req.project)
        phase = get_phase(req.project)
        return {"phase": phase or "intake"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/bead/{bead_id}")
async def api_get_bead(
    bead_id: str,
    _: str = Depends(verify_api_key),
):
    """Get a specific bead by ID."""
    try:
        bead = get_bead_by_id(bead_id)
        if not bead:
            raise HTTPException(404, f"Bead {bead_id} not found")
        return bead
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8002)
