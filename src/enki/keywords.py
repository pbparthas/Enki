"""Stop-word keyword extraction. No LLM. Pure heuristic."""

import re

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
    "in", "on", "at", "to", "for", "with", "from", "by", "of", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "over", "up", "down", "out", "off", "then",
    "just", "also", "very", "really", "quite", "rather",
    "implement", "add", "create", "build", "make", "fix", "update",
    "change", "modify", "write", "code", "file", "function", "class",
}


def extract_keywords(text: str, max_keywords: int = 6) -> list[str]:
    """Extract meaningful keywords from text. No LLM. Pure heuristic.

    Uses stop word removal + frequency. Returns unique keywords
    in order of appearance.
    """
    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9_-]*\b', text.lower())
    keywords = []
    seen = set()
    for word in words:
        if word not in STOP_WORDS and word not in seen and len(word) > 2:
            keywords.append(word)
            seen.add(word)
        if len(keywords) >= max_keywords:
            break
    return keywords
