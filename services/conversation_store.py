"""In-memory conversation store for the chat-workbench landing.

Single-process, 1h TTL per session. Per-v0.2.0 scope — Redis-backed upgrade
is a v0.2.1 task.

Stores last N user+assistant turns so the Sonnet orchestrator can ground
follow-up questions in prior context. Also carries attached-context
(meeting_id / doc_type / partij) chips across turns until user dismisses.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_TURNS_PER_SESSION = 6
SESSION_TTL_SECONDS = 60 * 60  # 1 hour
_SWEEP_INTERVAL_SECONDS = 5 * 60  # 5 min


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str
    attached_context: Dict[str, str] = field(default_factory=dict)


@dataclass
class ConversationState:
    session_id: str
    turns: List[Turn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_touched_at: float = field(default_factory=time.time)

    def append(self, role: str, content: str, attached_context: Optional[Dict[str, str]] = None) -> None:
        self.turns.append(Turn(role=role, content=content, attached_context=attached_context or {}))
        # Keep only last MAX_TURNS_PER_SESSION turns (pair of user+assistant = 2 turns)
        if len(self.turns) > MAX_TURNS_PER_SESSION * 2:
            self.turns = self.turns[-MAX_TURNS_PER_SESSION * 2 :]
        self.last_touched_at = time.time()

    def prior_messages(self) -> List[Dict[str, str]]:
        """Return prior turns in Anthropic messages[] shape (role, content)."""
        return [{"role": t.role, "content": t.content} for t in self.turns]


class ConversationStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, ConversationState] = {}
        self._sweep_task: Optional[asyncio.Task] = None

    def get_or_create(self, session_id: Optional[str]) -> ConversationState:
        if session_id and session_id in self._sessions:
            state = self._sessions[session_id]
            state.last_touched_at = time.time()
            return state
        sid = session_id or uuid.uuid4().hex
        state = ConversationState(session_id=sid)
        self._sessions[sid] = state
        return state

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def stats(self) -> Dict[str, int]:
        return {"active_sessions": len(self._sessions)}

    async def start_sweeper(self) -> None:
        if self._sweep_task and not self._sweep_task.done():
            return
        self._sweep_task = asyncio.create_task(self._sweep_loop())

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
                self._sweep_once()
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"conversation_store sweep failed: {e}")

    def _sweep_once(self) -> None:
        now = time.time()
        stale = [sid for sid, s in self._sessions.items() if now - s.last_touched_at > SESSION_TTL_SECONDS]
        for sid in stale:
            self._sessions.pop(sid, None)
        if stale:
            logger.info(f"conversation_store: swept {len(stale)} stale sessions ({len(self._sessions)} active)")


# Singleton — imported by routes/api.py and app startup
conversation_store = ConversationStore()
