import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import re
from typing import Dict, List, Optional, Any
from dataclasses import asdict

logger = logging.getLogger(__name__)

class EntityNormalizer:
    """
    Normalizes speaker names and party affiliations by matching them against 
    existing entities in the Knowledge Graph or historic statements.
    """
    
    def __init__(self, db_url: str = "postgresql://postgres:postgres@localhost:5432/neodemos"):
        self.db_url = db_url
        self.connection = None
        self._entity_cache = {} # Simple cache for the current run

    def _get_connection(self):
        if self.connection is None or self.connection.closed:
            try:
                self.connection = psycopg2.connect(self.db_url)
            except Exception as e:
                logger.warning(f"Could not connect to database for normalization: {e}")
                return None
        return self.connection

    def _strip_role(self, name: str) -> str:
        """
        Removes common roles and suffixes from speaker names.
        Example: "L.S. (Larissa) Vlieger (Commissievoorzitter)" -> "L.S. (Larissa) Vlieger"
        """
        if not name: return name
        # Remove anything in parentheses at the end that looks like a role
        roles = [
            r"\(Commissievoorzitter\)", r"\(Lid\)", r"\(Plaatsvervangend voorzitter\)",
            r"\(Wethouder\)", r"\(Burgemeester\)", r"\(Secretaris\)", r"\(Raadslid\)"
        ]
        clean_name = name
        for role in roles:
            clean_name = re.sub(role, "", clean_name, flags=re.IGNORECASE)
        
        # Also handle common prefixes
        prefixes = [r"^Wethouder\s+", r"^Voorzitter\s+", r"^Burgemeester\s+", r"^De heer\s+", r"^Mevrouw\s+"]
        for pref in prefixes:
            clean_name = re.sub(pref, "", clean_name, flags=re.IGNORECASE)
            
        return clean_name.strip()

    def correct_terms(self, text: str) -> str:
        """
        Fixes common mis-transcriptions of municipal terms.
        """
        if not text: return text
        corrections = {
            r"\bkoor\b": "COR",
            r"\bkor\b": "COR",
            r"\bibab\b": "iBabs",
            r"\bibabs\b": "iBabs",
            r"\broyal cast\b": "Royalcast",
            r"\broyalcast\b": "Royalcast",
            r"\braads voorstel\b": "raadsvoorstel",
            r"\bver vallen\b": "vervallen",
        }
        corrected = text
        for pattern, replacement in corrections.items():
            corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
        return corrected

    def normalize_speaker(self, name: str, party: Optional[str] = None) -> Dict[str, Any]:
        """
        Attempts to find a canonical entity for a speaker.
        Returns a dict with normalized fields and a 'mapped' flag.
        """
        if not name:
            return {"name": name, "party": party, "mapped": False}

        # 0. Strip roles for better matching
        search_name = self._strip_role(name)

        # Check cache first
        cache_key = f"{name}|{party or ''}"
        if cache_key in self._entity_cache:
            return self._entity_cache[cache_key]

        conn = self._get_connection()
        if not conn:
            return {"name": name, "party": party, "mapped": False}

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Direct match in kg_entities
                cur.execute(
                    "SELECT id, name, metadata FROM kg_entities WHERE (name = %s OR name = %s) AND type ILIKE '%%person%%'",
                    (search_name, name)
                )
                entity = cur.fetchone()
                if entity:
                    result = {
                        "name": entity['name'],
                        "party": entity['metadata'].get('fractie') or party,
                        "entity_id": entity['id'],
                        "mapped": True,
                        "source": "kg_entities"
                    }
                    self._entity_cache[cache_key] = result
                    return result

                # 2. Match in party_statements (historical data)
                cur.execute(
                    "SELECT speaker_name, party_name FROM party_statements WHERE (speaker_name = %s OR speaker_name = %s) LIMIT 1",
                    (search_name, name)
                )
                stmt = cur.fetchone()
                if stmt:
                    result = {
                        "name": stmt['speaker_name'],
                        "party": stmt['party_name'] or party,
                        "mapped": True,
                        "source": "party_statements"
                    }
                    self._entity_cache[cache_key] = result
                    return result

                # 3. Fuzzy match fallback (Placeholder for implementation with fuzzywuzzy or pg_trgm)
                # For now, we return the raw data but marked as unmapped
                return {"name": name, "party": party, "mapped": False}

        except Exception as e:
            logger.error(f"Error during speaker normalization: {e}")
            return {"name": name, "party": party, "mapped": False}

    def normalize_segments(self, segments: List[Any]) -> List[Any]:
        """
        Batch normalizes a list of objects (SpeakerSegment, DetectedSpeaker, or TranscriptSegment).
        Note: Modifies segments in place.
        """
        for seg in segments:
            # Check for 'speaker' or 'name' attribute
            name = getattr(seg, 'speaker', getattr(seg, 'name', None))
            party = getattr(seg, 'party', None)
            
            if name:
                norm = self.normalize_speaker(name, party)
                if norm.get("mapped"):
                    # Update whatever attribute we found
                    if hasattr(seg, 'speaker'):
                        seg.speaker = norm['name']
                    elif hasattr(seg, 'name'):
                        seg.name = norm['name']
                    
                    seg.party = norm['party']
                    
                    # Store mapping info in metadata if it exists
                    if hasattr(seg, 'metadata') and seg.metadata is not None:
                        if isinstance(seg.metadata, dict):
                            seg.metadata['normalized'] = True
                            seg.metadata['raw_name'] = name # Preserve original for provenance
                            seg.metadata['entity_id'] = norm.get('entity_id')
                            seg.metadata['norm_source'] = norm.get('source')
            
            # Also apply term correction to the text if it's a transcript segment
            if hasattr(seg, 'text') and seg.text:
                seg.text = self.correct_terms(seg.text)
        return segments

    def __del__(self):
        if self.connection:
            self.connection.close()
