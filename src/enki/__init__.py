"""Enki - Second brain for software engineering."""

__version__ = "0.1.0"

from .db import get_db, init_db
from .beads import create_bead, get_bead, update_bead, delete_bead
from .search import search
from .retention import calculate_weight

__all__ = [
    "get_db",
    "init_db",
    "create_bead",
    "get_bead",
    "update_bead",
    "delete_bead",
    "search",
    "calculate_weight",
]
