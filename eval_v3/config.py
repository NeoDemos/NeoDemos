"""
v3 Evaluation Configuration — extends v2 EvalConfig with routing parameters.
"""

from dataclasses import dataclass
from pathlib import Path

from eval.config import EvalConfig

V3_DIR = Path(__file__).parent
V3_RUNS_DIR = V3_DIR / "runs"


@dataclass
class V3EvalConfig(EvalConfig):
    """Extends v2 config with v3-specific routing and generation parameters."""

    # Routing
    enable_router: bool = True
    enable_map_reduce: bool = True
    enable_decomposition: bool = True

    # Models (all API-only)
    router_model: str = "claude-haiku-4-5-20251001"
    synthesis_model: str = "claude-sonnet-4-20250514"
    map_model: str = "gemini-2.5-flash-lite"

    # Generation strategy override (None = auto from router)
    force_strategy: str = ""  # "standard", "party_filtered", "map_reduce", "sub_query"

    # Override runs dir to eval_v3/runs/
    runs_dir: str = str(V3_RUNS_DIR)

    def snapshot(self) -> dict:
        base = super().snapshot()
        base.update({
            "enable_router": self.enable_router,
            "enable_map_reduce": self.enable_map_reduce,
            "enable_decomposition": self.enable_decomposition,
            "router_model": self.router_model,
            "synthesis_model": self.synthesis_model,
            "map_model": self.map_model,
            "force_strategy": self.force_strategy,
            "architecture": "v3",
        })
        return base
