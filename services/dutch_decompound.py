"""
Dutch Compound Word Decompounder

Splits Dutch compound words into their components using a dictionary-based
approach (same algorithm as Elasticsearch's dictionary_decompounder).

The word list is built from two sources:
1. Domain terms extracted from our own corpus (PostgreSQL tsvector stems)
2. A base Dutch word list (OpenTaal, loaded at startup if available)

Usage:
    from services.dutch_decompound import Decompounder
    dc = Decompounder()  # Loads lexicon on first use
    dc.decompose("leegstandsbelasting")  # → ["leegstand", "belasting"]
    dc.decompose("woningbouwprogramma")  # → ["woningbouw", "programma"]
    dc.expand_query("leegstand woningen")  # → "leegstand leegstandsbelasting leegstandsvisie woningen"
"""

import os
import logging
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Dutch interfixes that connect compound word parts
# e.g. leegstand-s-belasting, kind-er-en, boek-en-kast
INTERFIXES = ["", "s", "e", "en", "er", "n"]

# Minimum component length to avoid spurious splits
MIN_LEFT = 4
MIN_RIGHT = 5   # 5 prevents: "rdam", "matig", "len", "ling" as right part
MIN_COMPOUND_LEN = 9  # Don't try to split words shorter than this


class Decompounder:
    """Dutch compound word decompounder with lazy-loaded lexicon."""

    def __init__(self, db_url: str = ""):
        self._lexicon: Optional[Set[str]] = None
        self._db_url = db_url or os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        # Reverse index: for a stem like "leegstand", store all compound words
        # we've seen that contain it. Built lazily during decompose calls.
        self._compounds_by_stem: dict = {}

    @property
    def lexicon(self) -> Set[str]:
        if self._lexicon is None:
            self._lexicon = self._build_lexicon()
        return self._lexicon

    def _build_lexicon(self) -> Set[str]:
        """
        Build the lexicon from real word lists only.
        Does NOT use tsvector stems (they contain truncated fragments like
        'warmtebed' and 'woningbouwpro' which cause bad decomposition).
        """
        words = set()

        # OpenTaal word list (414K real Dutch words)
        opentaal_path = PROJECT_ROOT / "data" / "lexicons" / "opentaal_wordlist.txt"
        if opentaal_path.exists():
            try:
                with open(opentaal_path, encoding="utf-8") as f:
                    for line in f:
                        w = line.strip().lower()
                        if w and len(w) >= MIN_LEFT and not w.startswith("#"):
                            words.add(w)
                logger.info(f"Loaded OpenTaal, lexicon now {len(words)} words")
            except Exception as e:
                logger.warning(f"Could not load OpenTaal: {e}")

        # Domain terms from document titles (real words, not stemmed fragments)
        domain_path = PROJECT_ROOT / "data" / "lexicons" / "domain_terms.txt"
        if domain_path.exists():
            try:
                with open(domain_path, encoding="utf-8") as f:
                    for line in f:
                        w = line.strip().lower()
                        if w and len(w) >= MIN_LEFT:
                            words.add(w)
                logger.info(f"Loaded domain terms, lexicon now {len(words)} words")
            except Exception as e:
                logger.warning(f"Could not load domain terms: {e}")

        # Common Dutch municipal terms (always available as fallback)
        _MUNICIPAL_TERMS = {
            "leegstand", "belasting", "verordening", "visie", "aanpak",
            "woning", "woningbouw", "begroting", "motie", "amendement",
            "raad", "raadslid", "college", "wethouder", "commissie",
            "programma", "subsidie", "bijstand", "uitkering", "schuld",
            "schuldhulp", "jeugd", "jeugdzorg", "onderwijs", "sport",
            "cultuur", "warmte", "energie", "klimaat", "mobiliteit",
            "haven", "veiligheid", "bestuur", "financieel", "jaarrekening",
            "buitenruimte", "groen", "water", "sociaal", "welzijn",
            "zorg", "armoede", "werk", "inkomen", "integratie",
            "huisvesting", "huur", "koop", "nieuwbouw", "renovatie",
            "sloop", "transformatie", "gebiedsontwikkeling", "stadsvernieuwing",
            "verkeer", "fiets", "openbaar", "vervoer", "metro", "tram",
            "parkeer", "milieu", "afval", "riool", "erfgoed", "monument",
            "evenement", "horeca", "nacht", "festival", "markt",
            "handhaving", "toezicht", "politie", "brandweer",
        }
        words.update(_MUNICIPAL_TERMS)

        if len(words) < 1000:
            logger.warning(f"Lexicon only has {len(words)} words — quality may be low")

        return words

    def decompose(self, word: str) -> List[str]:
        """
        Split a Dutch compound word into components.
        Returns [word] if no valid decomposition found.

        Uses greedy longest-left-match with Dutch interfix handling.
        """
        w = word.lower().strip()
        if len(w) < MIN_COMPOUND_LEN:
            return [w]
        if w in self.lexicon:
            # Word itself is in lexicon — might still be a compound
            # Only decompose if we find a valid split
            pass

        best = None
        for i in range(MIN_LEFT, len(w) - MIN_RIGHT + 1):
            left = w[:i]
            if left not in self.lexicon:
                continue
            remainder = w[i:]
            for ifix in INTERFIXES:
                if not remainder.startswith(ifix):
                    continue
                right = remainder[len(ifix):]
                if len(right) < MIN_RIGHT:
                    continue
                if right in self.lexicon:
                    # Prefer longest left component (greedy)
                    if best is None or len(left) > len(best[0]):
                        best = (left, right)

        if best:
            # Record in reverse index
            for part in best:
                self._compounds_by_stem.setdefault(part, set()).add(w)
            return list(best)

        return [w]

    def expand_query(self, query: str) -> str:
        """
        Expand a search query with compound word variants.
        For each word in the query, find compound words in the lexicon
        that contain it as a component.

        'leegstand woningen' → 'leegstand leegstandsbelasting leegstandsvisie woningen'
        """
        words = query.lower().replace("?", "").replace(",", "").split()
        expanded = list(words)

        for word in words:
            if len(word) < MIN_LEFT:
                continue
            # Find compounds that start with this word
            compounds = self._find_compounds_starting_with(word)
            for c in compounds:
                if c not in expanded:
                    expanded.append(c)

        return " ".join(expanded)

    def _find_compounds_starting_with(self, stem: str) -> List[str]:
        """Find compound words in the lexicon that start with this stem."""
        # Check cached reverse index first
        if stem in self._compounds_by_stem:
            return list(self._compounds_by_stem[stem])

        # Scan lexicon for compounds starting with stem
        # (only for words significantly longer than the stem)
        results = []
        min_len = len(stem) + MIN_RIGHT
        for word in self.lexicon:
            if len(word) >= min_len and word.startswith(stem) and word != stem:
                # Validate it's a real compound (not just a longer word)
                remainder = word[len(stem):]
                for ifix in INTERFIXES:
                    if remainder.startswith(ifix):
                        right = remainder[len(ifix):]
                        if right in self.lexicon and len(right) >= MIN_RIGHT:
                            results.append(word)
                            break

        # Cache
        self._compounds_by_stem[stem] = set(results)
        return results


# Module-level singleton
_decompounder: Optional[Decompounder] = None


def get_decompounder() -> Decompounder:
    """Get the singleton decompounder instance."""
    global _decompounder
    if _decompounder is None:
        _decompounder = Decompounder()
    return _decompounder


def decompound_text(text: str, min_word_len: int = 8) -> str:
    """
    Extract decomposed terms from a text for BM25 indexing.

    Only processes words >= min_word_len chars (shorter words rarely compound).
    Returns space-separated string of decomposed PARTS that differ from the
    original word. Suitable for a `decomposed_terms` column.

    Example:
        decompound_text("De leegstandsbelasting in de gemeenteraad")
        → "leegstand belasting gemeente raad"
    """
    import re

    if not text:
        return ""

    dc = get_decompounder()
    words = re.findall(r'[a-zA-ZÀ-ÿ]+', text.lower())
    decomposed = set()

    for word in words:
        if len(word) < min_word_len:
            continue
        parts = dc.decompose(word)
        if len(parts) > 1:
            for part in parts:
                if part != word:
                    decomposed.add(part)

    return " ".join(sorted(decomposed))
