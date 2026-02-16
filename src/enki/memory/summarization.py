"""summarization.py — Weekly/monthly digests of knowledge activity.

Heuristic grouping and reporting. Read-only — does not create or modify beads.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from enki.db import abzu_db, wisdom_db


def generate_weekly_digest(project: str | None = None, as_json: bool = False) -> str | dict:
    """Generate digest of knowledge activity for the past 7 days.

    Includes:
    - New beads created (count + summaries)
    - Beads promoted from staging (count + summaries)
    - Beads decayed below threshold (count)
    - Top 5 most recalled beads (most accessed)
    - Staging rejections (count + common reasons)
    - Cross-project patterns (if multiple projects active)
    """
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    data: dict = {}

    # New beads in wisdom.db
    with wisdom_db() as conn:
        project_clause = " AND project = ?" if project else ""
        params: list = [cutoff]
        if project:
            params.append(project)

        new_beads = conn.execute(
            f"SELECT id, content, category, project, created_at "
            f"FROM beads WHERE created_at >= ?{project_clause} "
            f"ORDER BY created_at DESC",
            params,
        ).fetchall()
        data["new_beads"] = [dict(r) for r in new_beads]

        # Promoted beads (have promoted_at timestamp)
        promoted = conn.execute(
            f"SELECT id, content, category, promoted_at "
            f"FROM beads WHERE promoted_at >= ?{project_clause} "
            f"ORDER BY promoted_at DESC",
            params,
        ).fetchall()
        data["promoted"] = [dict(r) for r in promoted]

        # Decayed beads (weight dropped below 0.5)
        decayed = conn.execute(
            f"SELECT COUNT(*) FROM beads WHERE weight < 0.5{project_clause}",
            params[1:] if project else [],
        ).fetchone()
        data["decayed_count"] = decayed[0] if decayed else 0

        # Top accessed beads
        top_accessed = conn.execute(
            "SELECT id, content, category, last_accessed "
            "FROM beads WHERE last_accessed >= ? "
            "ORDER BY last_accessed DESC LIMIT 5",
            (cutoff,),
        ).fetchall()
        data["top_accessed"] = [dict(r) for r in top_accessed]

        # Cross-project: count beads per project
        project_counts = conn.execute(
            "SELECT project, COUNT(*) as cnt FROM beads "
            "WHERE created_at >= ? AND project IS NOT NULL "
            "GROUP BY project ORDER BY cnt DESC",
            (cutoff,),
        ).fetchall()
        data["cross_project"] = [dict(r) for r in project_counts]

    # Staging rejections
    try:
        with abzu_db() as conn:
            rejections = conn.execute(
                "SELECT reason, COUNT(*) as cnt FROM staging_rejections "
                "WHERE rejected_at >= ? GROUP BY reason ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
            data["rejections"] = [dict(r) for r in rejections]
            total_rejections = sum(r["cnt"] for r in rejections)
            data["rejection_total"] = total_rejections
    except Exception:
        data["rejections"] = []
        data["rejection_total"] = 0

    if as_json:
        return data

    # Format as text
    lines = ["# Weekly Digest", ""]

    lines.append(f"**New beads:** {len(data['new_beads'])}")
    for b in data["new_beads"][:10]:
        lines.append(f"  - [{b['category']}] {b['content'][:80]}")

    lines.append(f"\n**Promoted from staging:** {len(data['promoted'])}")
    for b in data["promoted"][:5]:
        lines.append(f"  - [{b['category']}] {b['content'][:80]}")

    lines.append(f"\n**Beads below decay threshold:** {data['decayed_count']}")

    if data["top_accessed"]:
        lines.append(f"\n**Most accessed (past 7 days):**")
        for b in data["top_accessed"]:
            lines.append(f"  - [{b['category']}] {b['content'][:80]}")

    if data["rejection_total"] > 0:
        lines.append(f"\n**Bouncer rejections:** {data['rejection_total']}")
        for r in data["rejections"]:
            lines.append(f"  - {r['reason']}: {r['cnt']}")

    if len(data["cross_project"]) > 1:
        lines.append(f"\n**Cross-project activity:**")
        for p in data["cross_project"]:
            lines.append(f"  - {p['project']}: {p['cnt']} beads")

    if not data["new_beads"] and not data["promoted"]:
        lines.append("\nNo knowledge activity in the past 7 days.")

    return "\n".join(lines)


def generate_monthly_synthesis(project: str | None = None, as_json: bool = False) -> str | dict:
    """Generate monthly synthesis — consolidate scattered beads into themes.

    Process:
    1. Group beads created in last 30 days by category
    2. Within each category, cluster by keyword overlap
    3. For clusters with 3+ beads, generate a synthesis title
    4. Report only — does NOT create or modify beads.
    """
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    with wisdom_db() as conn:
        project_clause = " AND project = ?" if project else ""
        params: list = [cutoff]
        if project:
            params.append(project)

        beads = conn.execute(
            f"SELECT id, content, category, project, created_at "
            f"FROM beads WHERE created_at >= ?{project_clause} "
            f"ORDER BY category, created_at DESC",
            params,
        ).fetchall()
        beads = [dict(r) for r in beads]

    if not beads:
        if as_json:
            return {"categories": {}, "themes": [], "total": 0}
        return "# Monthly Synthesis\n\nNo beads created in the past 30 days."

    # Group by category
    by_category: dict[str, list[dict]] = defaultdict(list)
    for b in beads:
        by_category[b["category"]].append(b)

    # Extract keywords from content (simple: words > 3 chars, not stopwords)
    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "have", "will",
        "been", "were", "they", "their", "your", "about", "into", "more",
        "than", "each", "when", "what", "also", "just", "should", "could",
        "would", "does", "don't", "didn't", "it's", "using", "used",
    }

    def extract_keywords(text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z]{4,}", text.lower())
        return {w for w in words if w not in stopwords}

    # Cluster beads by keyword overlap within each category
    themes = []
    for category, cat_beads in by_category.items():
        # Build keyword index
        bead_keywords = [(b, extract_keywords(b["content"])) for b in cat_beads]

        # Simple clustering: group beads that share 2+ keywords
        used = set()
        for i, (bead_a, kw_a) in enumerate(bead_keywords):
            if i in used:
                continue
            cluster = [bead_a]
            cluster_keywords = Counter(kw_a)
            used.add(i)

            for j, (bead_b, kw_b) in enumerate(bead_keywords):
                if j in used:
                    continue
                overlap = kw_a & kw_b
                if len(overlap) >= 2:
                    cluster.append(bead_b)
                    cluster_keywords.update(kw_b)
                    used.add(j)

            if len(cluster) >= 3:
                # Use top keywords as theme name
                top_kw = [w for w, _ in cluster_keywords.most_common(3)]
                theme_name = ", ".join(top_kw)
                themes.append({
                    "category": category,
                    "theme": theme_name,
                    "count": len(cluster),
                    "bead_ids": [b["id"] for b in cluster],
                    "sample": cluster[0]["content"][:100],
                })

    data = {
        "categories": {cat: len(bs) for cat, bs in by_category.items()},
        "themes": themes,
        "total": len(beads),
    }

    if as_json:
        return data

    # Format as text
    lines = ["# Monthly Synthesis", ""]
    lines.append(f"**Total beads (past 30 days):** {len(beads)}")
    lines.append("")

    lines.append("**By category:**")
    for cat, count in sorted(data["categories"].items(), key=lambda x: -x[1]):
        lines.append(f"  - {cat}: {count}")

    if themes:
        lines.append(f"\n**Themes detected ({len(themes)}):**")
        for t in themes:
            lines.append(
                f"  - [{t['category']}] {t['theme']} ({t['count']} beads)"
            )
            lines.append(f"    Sample: \"{t['sample']}\"")
    else:
        lines.append("\nNo strong themes detected (need 3+ related beads).")

    return "\n".join(lines)


def synthesize_knowledge(
    project: str | None = None,
    min_cluster_size: int = 3,
    auto_apply: bool = False,
) -> list[dict]:
    """Consolidate clusters of related beads into synthesis candidates.

    Process:
    1. Get all beads from last 90 days
    2. Group by category
    3. Within each category, cluster by keyword overlap (3+ shared nouns)
    4. For clusters >= min_cluster_size:
       a. Generate synthesis title from shared keywords
       b. Concatenate key sentences from each bead
       c. Create a staging candidate (goes through normal staging → promotion)
       d. Mark source beads with synthesis_id

    Synthesis beads go to staging (abzu.db), NOT directly to wisdom.db.
    Returns: list of synthesis dicts with cluster info.
    """
    from enki.memory.staging import add_candidate

    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")

    with wisdom_db() as conn:
        project_clause = " AND project = ?" if project else ""
        params: list = [cutoff]
        if project:
            params.append(project)

        beads = conn.execute(
            f"SELECT id, content, category, project, weight, created_at "
            f"FROM beads WHERE created_at >= ? AND synthesis_id IS NULL"
            f"{project_clause} "
            f"ORDER BY category, created_at DESC",
            params,
        ).fetchall()
        beads = [dict(r) for r in beads]

    if not beads:
        return []

    # Group by category
    by_category: dict[str, list[dict]] = defaultdict(list)
    for b in beads:
        by_category[b["category"]].append(b)

    # Extract keywords: words > 3 chars, not stopwords, includes code identifiers
    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "have", "will",
        "been", "were", "they", "their", "your", "about", "into", "more",
        "than", "each", "when", "what", "also", "just", "should", "could",
        "would", "does", "using", "used", "make", "like", "need", "want",
    }

    def extract_nouns(text: str) -> set[str]:
        """Extract meaningful words including camelCase/snake_case identifiers."""
        # Split camelCase
        text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
        # Split snake_case
        text = text.replace('_', ' ')
        words = re.findall(r'[a-zA-Z]{4,}', text.lower())
        return {w for w in words if w not in stopwords}

    # Cluster beads by keyword overlap within each category
    syntheses = []
    for category, cat_beads in by_category.items():
        bead_nouns = [(b, extract_nouns(b["content"])) for b in cat_beads]

        used = set()
        for i, (bead_a, nouns_a) in enumerate(bead_nouns):
            if i in used:
                continue
            cluster = [bead_a]
            cluster_nouns = Counter(nouns_a)
            used.add(i)

            for j, (bead_b, nouns_b) in enumerate(bead_nouns):
                if j in used:
                    continue
                shared = nouns_a & nouns_b
                if len(shared) >= 3:
                    cluster.append(bead_b)
                    cluster_nouns.update(nouns_b)
                    used.add(j)

            if len(cluster) < min_cluster_size:
                continue

            # Generate synthesis
            top_keywords = [w for w, _ in cluster_nouns.most_common(5)]
            theme = ", ".join(top_keywords[:3])

            # Extract first meaningful sentence from each bead
            sentences = []
            for b in cluster:
                content = b["content"].strip()
                # Take first sentence or first 100 chars
                first_sent = re.split(r'[.!?\n]', content)[0].strip()
                if first_sent and len(first_sent) > 10:
                    sentences.append(first_sent)

            # Build consolidated content
            synth_content = f"Synthesis: {theme}\n\n"
            synth_content += f"Based on {len(cluster)} {category} beads:\n"
            for sent in sentences[:10]:
                synth_content += f"- {sent}\n"
            synth_content += f"\nKeywords: {', '.join(top_keywords)}"

            avg_weight = sum(b.get("weight", 1.0) for b in cluster) / len(cluster)
            source_ids = [b["id"] for b in cluster]

            synthesis = {
                "category": category,
                "theme": theme,
                "content": synth_content,
                "source_bead_ids": source_ids,
                "count": len(cluster),
                "avg_weight": round(avg_weight, 2),
            }

            if auto_apply:
                # Stage the synthesis (goes through normal pipeline)
                candidate_id = add_candidate(
                    content=synth_content,
                    category="learning",  # synthesis stored as learning
                    project=project,
                    summary=f"Synthesis: {theme} ({len(cluster)} beads)",
                    source="synthesis",
                )
                synthesis["candidate_id"] = candidate_id

                # Mark source beads with a synthesis marker
                if candidate_id:
                    with wisdom_db() as conn:
                        for bead_id in source_ids:
                            conn.execute(
                                "UPDATE beads SET synthesis_id = ? WHERE id = ?",
                                (candidate_id, bead_id),
                            )

            syntheses.append(synthesis)

    return syntheses


def generate_short_digest(project: str | None = None) -> str:
    """Short 3-4 line digest for session-start injection.

    Used when 7+ days have passed since last session.
    """
    data = generate_weekly_digest(project=project, as_json=True)
    if not isinstance(data, dict):
        return ""

    new_count = len(data.get("new_beads", []))
    promoted_count = len(data.get("promoted", []))
    rejection_count = data.get("rejection_total", 0)

    if new_count == 0 and promoted_count == 0:
        return "No knowledge activity since last session."

    parts = []
    if new_count > 0:
        parts.append(f"{new_count} new bead{'s' if new_count != 1 else ''}")
    if promoted_count > 0:
        parts.append(f"{promoted_count} promoted")
    if rejection_count > 0:
        parts.append(f"{rejection_count} rejected by bouncer")

    return f"Since last session: {', '.join(parts)}."
