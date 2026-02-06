"""Storage abstraction for remote/local memory operations.

P2-10: Eliminates the duplicated `if remote: ... else: ...` pattern
in MCP server handlers. MCP server calls a single interface;
implementation routes to beads.py (local) or client.py (remote).

Usage:
    store = get_store()
    result = store.remember(content="...", bead_type="learning")
    results = store.recall(query="...")
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class MemoryStore(ABC):
    """Abstract interface for memory operations (local or remote)."""

    @abstractmethod
    def remember(self, content: str, bead_type: str, *,
                 summary: str = None, project: str = None,
                 context: str = None, tags: list = None,
                 starred: bool = False) -> dict:
        """Store a knowledge bead.

        Returns: {"id": str, "type": str, "content": str, "offline": bool, "fallback": bool}
        """
        ...

    @abstractmethod
    def recall(self, query: str, *, project: str = None,
               bead_type: str = None, limit: int = 10) -> list[dict]:
        """Search for knowledge.

        Returns: list of {"id": str, "type": str, "content": str, "summary": str,
                          "score": float, "sources": str, "starred": bool, "cached": bool}
        """
        ...

    @abstractmethod
    def supersede(self, old_id: str, new_id: str) -> dict:
        """Mark a bead as superseded.

        Returns: {"old_id": str, "new_id": str, "found": bool, "offline": bool, "fallback": bool}
        """
        ...

    @abstractmethod
    def star(self, bead_id: str, starred: bool = True) -> dict:
        """Star or unstar a bead.

        Returns: {"bead_id": str, "starred": bool, "found": bool, "offline": bool, "fallback": bool}
        """
        ...

    @abstractmethod
    def get_status(self, project: str = None) -> dict:
        """Get memory status.

        Returns: {"phase": str, "goal": str, "total_beads": int,
                  "active_beads": int, "starred_beads": int, "offline": bool, "fallback": bool, ...}
        """
        ...

    @abstractmethod
    def set_goal(self, goal: str, project: str = None) -> dict:
        """Set session goal.

        Returns: {"goal": str, "offline": bool, "fallback": bool}
        """
        ...

    @abstractmethod
    def get_or_set_phase(self, phase: str = None, project: str = None) -> dict:
        """Get or set current phase.

        Returns: {"phase": str, "offline": bool, "fallback": bool}
        """
        ...


class LocalMemoryStore(MemoryStore):
    """Local storage using SQLite (beads.py, search.py, session.py)."""

    def remember(self, content, bead_type, *, summary=None, project=None,
                 context=None, tags=None, starred=False):
        from .beads import create_bead
        bead = create_bead(
            content=content, bead_type=bead_type, summary=summary,
            project=project, context=context, tags=tags, starred=starred,
        )
        return {
            "id": bead.id, "type": bead.type, "content": bead.content,
            "offline": False, "fallback": False,
        }

    def recall(self, query, *, project=None, bead_type=None, limit=10):
        from .search import search
        results = search(query=query, project=project, bead_type=bead_type, limit=limit)
        return [
            {
                "id": r.bead.id, "type": r.bead.type,
                "content": r.bead.content,
                "summary": r.bead.summary or r.bead.content[:150],
                "score": r.score,
                "sources": "+".join(r.sources),
                "starred": r.bead.starred,
                "cached": False,
            }
            for r in results
        ]

    def supersede(self, old_id, new_id):
        from .beads import supersede_bead
        bead = supersede_bead(old_id, new_id)
        return {
            "old_id": old_id, "new_id": new_id,
            "found": bead is not None,
            "offline": False, "fallback": False,
        }

    def star(self, bead_id, starred=True):
        from .beads import star_bead, unstar_bead
        bead = star_bead(bead_id) if starred else unstar_bead(bead_id)
        return {
            "bead_id": bead_id, "starred": starred,
            "found": bead is not None,
            "offline": False, "fallback": False,
        }

    def get_status(self, project=None):
        from pathlib import Path
        from .db import get_db
        from .session import get_session, get_phase, get_goal
        from .orchestrator import get_full_orchestration_status

        project_path = Path(project) if project else None
        db = get_db()
        total = db.execute("SELECT COUNT(*) as count FROM beads").fetchone()["count"]
        active = db.execute("SELECT COUNT(*) as count FROM beads WHERE superseded_by IS NULL").fetchone()["count"]
        starred_count = db.execute("SELECT COUNT(*) as count FROM beads WHERE starred = 1").fetchone()["count"]
        session = get_session(project_path)
        phase = get_phase(project_path) if session else "intake"
        goal = get_goal(project_path) if session else None
        orch = get_full_orchestration_status(project_path)

        return {
            "phase": phase, "goal": goal,
            "total_beads": total, "active_beads": active, "starred_beads": starred_count,
            "orchestration": orch,
            "offline": False, "fallback": False,
        }

    def set_goal(self, goal, project=None):
        from pathlib import Path
        from .session import set_goal as _set_goal
        project_path = Path(project) if project else None
        _set_goal(goal, project_path)
        return {"goal": goal, "offline": False, "fallback": False}

    def get_or_set_phase(self, phase=None, project=None):
        from pathlib import Path
        from .session import get_phase, set_phase
        project_path = Path(project) if project else None
        if phase:
            set_phase(phase, project_path)
            return {"phase": phase, "offline": False, "fallback": False}
        current = get_phase(project_path)
        return {"phase": current, "offline": False, "fallback": False}


class RemoteMemoryStore(MemoryStore):
    """Remote storage via API client, with automatic local fallback."""

    def __init__(self):
        self._local = LocalMemoryStore()

    def _with_fallback(self, remote_fn, local_fn):
        """Try remote, fall back to local on failure."""
        try:
            result = remote_fn()
            return result
        except Exception:
            result = local_fn()
            result["fallback"] = True
            return result

    def remember(self, content, bead_type, *, summary=None, project=None,
                 context=None, tags=None, starred=False):
        from .client import remote_remember

        def _remote():
            result = remote_remember(
                content=content, bead_type=bead_type, summary=summary,
                project=project, context=context, tags=tags, starred=starred,
            )
            return {
                "id": result["id"], "type": bead_type, "content": content,
                "offline": bool(result.get("offline")), "fallback": False,
            }

        def _local():
            return self._local.remember(
                content, bead_type, summary=summary, project=project,
                context=context, tags=tags, starred=starred,
            )

        return self._with_fallback(_remote, _local)

    def recall(self, query, *, project=None, bead_type=None, limit=10):
        from .client import remote_recall

        try:
            results = remote_recall(query=query, project=project, bead_type=bead_type, limit=limit)
            return [
                {
                    "id": r["id"], "type": r["type"],
                    "content": r.get("content", ""),
                    "summary": r.get("summary") or r.get("content", "")[:150],
                    "score": r.get("score", 0),
                    "sources": "remote",
                    "starred": False,
                    "cached": bool(r.get("cached")),
                }
                for r in results
            ]
        except Exception:
            return self._local.recall(query, project=project, bead_type=bead_type, limit=limit)

    def supersede(self, old_id, new_id):
        from .client import remote_supersede

        def _remote():
            result = remote_supersede(old_id, new_id)
            return {
                "old_id": old_id, "new_id": new_id, "found": True,
                "offline": bool(result.get("offline")), "fallback": False,
            }

        return self._with_fallback(_remote, lambda: self._local.supersede(old_id, new_id))

    def star(self, bead_id, starred=True):
        from .client import remote_star

        def _remote():
            result = remote_star(bead_id, starred)
            return {
                "bead_id": bead_id, "starred": starred, "found": True,
                "offline": bool(result.get("offline")), "fallback": False,
            }

        return self._with_fallback(_remote, lambda: self._local.star(bead_id, starred))

    def get_status(self, project=None):
        from .client import remote_status
        from .offline import is_offline, get_cache_count, get_queue_size

        try:
            status = remote_status(project)
            return {
                "phase": status.get("phase", "intake"),
                "goal": status.get("goal"),
                "total_beads": status.get("total_beads", 0),
                "active_beads": status.get("active_beads", 0),
                "starred_beads": status.get("starred_beads", 0),
                "cached_beads": status.get("cached_beads", get_cache_count()),
                "pending_sync": status.get("pending_sync", get_queue_size()),
                "orchestration": None,
                "offline": bool(status.get("offline") or is_offline()),
                "fallback": False,
            }
        except Exception:
            result = self._local.get_status(project)
            result["fallback"] = True
            return result

    def set_goal(self, goal, project=None):
        from .client import remote_goal

        def _remote():
            result = remote_goal(goal, project)
            return {
                "goal": goal,
                "offline": bool(result.get("offline")),
                "fallback": False,
            }

        return self._with_fallback(_remote, lambda: self._local.set_goal(goal, project))

    def get_or_set_phase(self, phase=None, project=None):
        from .client import remote_phase

        try:
            result = remote_phase(phase, project)
            return {
                "phase": result.get("phase", phase or "intake"),
                "offline": bool(result.get("offline")),
                "fallback": False,
            }
        except Exception:
            result = self._local.get_or_set_phase(phase, project)
            result["fallback"] = True
            return result


def get_store(remote: bool = None) -> MemoryStore:
    """Get the appropriate store based on remote mode setting.

    Args:
        remote: Override remote mode. If None, uses is_remote_mode() (env-based).

    Returns RemoteMemoryStore if remote mode is enabled,
    LocalMemoryStore otherwise.
    """
    if remote is None:
        from .client import is_remote_mode
        remote = is_remote_mode()
    if remote:
        return RemoteMemoryStore()
    return LocalMemoryStore()
