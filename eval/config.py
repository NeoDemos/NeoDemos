"""Shared configuration for the RAG evaluation pipeline."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = PROJECT_ROOT / "eval"
RUNS_DIR = EVAL_DIR / "runs"
DATA_DIR = EVAL_DIR / "data"


@dataclass
class EvalConfig:
    questions_path: str = str(DATA_DIR / "questions.json")
    runs_dir: str = str(RUNS_DIR)
    run_id: str = ""
    compare_with: str = ""
    category_filter: str = ""
    skip_generation: bool = False
    fast_mode: bool = False
    top_k: int = 10
    score_threshold: float = 0.15
    reranker_threshold: float = -2.0
    anthropic_model: str = "claude-sonnet-4-20250514"
    hallucination_mode: bool = False  # Run claim-level verification
    db_url: str = ""

    def __post_init__(self):
        if not self.db_url:
            self.db_url = os.getenv(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5432/neodemos",
            )
        if not self.run_id:
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def labeled_run_id(self, label: str) -> str:
        return f"{self.run_id}_{label}" if label else self.run_id

    def run_dir(self) -> Path:
        p = Path(self.runs_dir) / self.run_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot of tunable parameters."""
        return {
            "top_k": self.top_k,
            "fast_mode": self.fast_mode,
            "score_threshold": self.score_threshold,
            "reranker_threshold": self.reranker_threshold,
            "anthropic_model": self.anthropic_model,
            "skip_generation": self.skip_generation,
            "hallucination_mode": self.hallucination_mode,
            "category_filter": self.category_filter,
        }
