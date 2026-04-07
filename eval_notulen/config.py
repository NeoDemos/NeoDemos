"""Configuration for virtual notulen evaluation and audit runs."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(__file__).resolve().parent / "runs"


@dataclass
class NotulenAuditConfig:
    """Configuration for a single virtual notulen audit run."""

    # ── Database ───────────────────────────────────────────────────────────
    db_url: str = ""
    staging_schema: str = "staging"

    # ── Qdrant ────────────────────────────────────────────────────────────
    qdrant_url: str = ""
    qdrant_staging_collection: str = "committee_transcripts_staging"

    # ── Lexicon ───────────────────────────────────────────────────────────
    lexicon_path: str = ""

    # ── LLM judge ─────────────────────────────────────────────────────────
    # Backend: "gemini" (default, cheapest API), "claude", or "local" (Qwen MLX)
    judge_backend: str = "gemini"
    anthropic_model: str = "claude-haiku-4-5-20251001"
    gemini_model: str = "gemini-2.5-flash-lite"  # newest generation Flash Lite
    local_model: str = "mlx-community/Qwen2.5-7B-Instruct-4bit"  # already downloaded

    # ── Quality thresholds ────────────────────────────────────────────────
    # Stricter than base eval (10%) — councillors require higher trust
    max_hallucination_rate: float = 0.05
    min_speaker_attribution_rate: float = 0.80
    min_quality_score: float = 0.70

    def __post_init__(self):
        if not self.db_url:
            self.db_url = os.getenv(
                "DB_URL",
                os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos"),
            )
        if not self.qdrant_url:
            self.qdrant_url = os.getenv("QDRANT_LOCAL_URL", "http://localhost:6333")
        if not self.lexicon_path:
            self.lexicon_path = str(
                PROJECT_ROOT / "data" / "lexicons" / "rotterdam_political_dictionary.json"
            )

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of tunable parameters."""
        return {
            "qdrant_staging_collection": self.qdrant_staging_collection,
            "max_hallucination_rate": self.max_hallucination_rate,
            "min_speaker_attribution_rate": self.min_speaker_attribution_rate,
            "min_quality_score": self.min_quality_score,
            "judge_backend": self.judge_backend,
            "anthropic_model": self.anthropic_model,
            "gemini_model": self.gemini_model,
        }
