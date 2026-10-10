"""
Agent Governance & Guardrails.
Detects runaway loops and enforces tool call whitelisting.
"""

import logging
from typing import List, Dict, Tuple, Optional
import time
from fastapi import HTTPException
from gateway.models.request import ChatCompletionRequest
from gateway.models.key import VirtualKey

logger = logging.getLogger(__name__)

class AgentGuard:
    """Provides safeguards for autonomous agents."""
    
    def __init__(self, max_requests_per_session_window: int = 20, session_window_sec: float = 60.0):
        self.max_requests_per_session_window = max_requests_per_session_window
        self.session_window_sec = session_window_sec
        # Store session_id -> list of timestamps
        self._session_history: Dict[str, List[float]] = {}
        logger.info("Initialized AgentGuard (Runaway Loop Detection & Tool Governance)")
        
    def _clean_session_history(self, session_id: str, now: float) -> None:
        """Removes expired timestamps from session history."""
        if session_id not in self._session_history:
            return
            
        cutoff = now - self.session_window_sec
        self._session_history[session_id] = [
            ts for ts in self._session_history[session_id] if ts > cutoff
        ]
        
        if not self._session_history[session_id]:
            del self._session_history[session_id]
            
    def track_session(self, session_id: str) -> None:
        """Track a request against a session ID to detect tight loops."""
        now = time.time()
        self._clean_session_history(session_id, now)
        
        if session_id not in self._session_history:
            self._session_history[session_id] = []
            
        history = self._session_history[session_id]
        
        if len(history) >= self.max_requests_per_session_window:
            logger.warning(f"Runaway loop detected for session {session_id}. Blocked.")
            raise HTTPException(
                status_code=429,
                detail=f"Runaway Loop Detected. Maximum {self.max_requests_per_session_window} requests per {self.session_window_sec}s exceeded for session {session_id}."
            )
            
        history.append(now)

    def validate_tools(self, request: ChatCompletionRequest, key: VirtualKey) -> None:
        """Enforce tool whitelisting based on VirtualKey permissions."""
        if not request.tools:
            return
            
        allowed_tools = getattr(key.permissions, 'allowed_tools', ["*"])
        if "*" in allowed_tools:
            return
            
        for tool in request.tools:
            if "function" in tool and "name" in tool["function"]:
                func_name = tool["function"]["name"]
                if func_name not in allowed_tools:
                    logger.warning(f"Blocked unauthorized tool call: {func_name} by key {key.id}")
                    raise HTTPException(
                        status_code=403, 
                        detail=f"Tool call '{func_name}' is not authorized for this API key."
                    )
