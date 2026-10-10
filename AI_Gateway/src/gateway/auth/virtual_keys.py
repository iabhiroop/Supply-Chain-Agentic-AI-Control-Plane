"""
Virtual API Key Management.
In-memory store for development mode.
"""

import logging
from uuid import UUID
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from gateway.models.key import VirtualKey, KeyCreate, KeyPermissions, RateLimits

logger = logging.getLogger(__name__)

class KeyManager:
    """Manages virtual keys using an in-memory dictionary."""
    def __init__(self, db: Optional['DatabaseRepository'] = None):
        self.db = db
        # Maps key_hash to VirtualKey (fallback for in-memory)
        self._keys: Dict[str, VirtualKey] = {}
        
        if self.db:
            logger.info("Initialized DB-backed KeyManager")
        else:
            logger.info("Initialized in-memory KeyManager")
            # Create a default admin key if running in dev mode
            self._create_default_dev_key()

    def _create_default_dev_key(self):
        """Creates a default key for local development."""
        raw_key, key_hash = VirtualKey.generate_key("vk_dev")
        
        vk = VirtualKey(
            key_prefix="vk_dev",
            key_hash=key_hash,
            name="Default Dev Key",
            description="Auto-generated key for development",
            organization_id="default",
            team_id="dev",
            project_id="default",
            permissions=KeyPermissions(allowed_models=["*"]),
            rate_limits=RateLimits(rpm=1000, tpm=1000000)
        )
        self._keys[key_hash] = vk
        logger.info(f"Created default dev key: {raw_key} (Use this in Authorization header)")

    async def create_key(self, request: KeyCreate) -> Tuple[str, VirtualKey]:
        """Generate a new virtual key."""
        raw_key, key_hash = VirtualKey.generate_key("vk")
        
        if self.db:
            vk = await self.db.create_key(raw_key, key_hash, request)
            logger.info(f"Created new virtual key in DB: {vk.id}")
            return raw_key, vk
            
        vk = VirtualKey(
            key_prefix="vk",
            key_hash=key_hash,
            name=request.name,
            description=request.description,
            organization_id=request.organization_id,
            team_id=request.team_id,
            project_id=request.project_id,
            agent_id=request.agent_id,
            permissions=request.permissions,
            rate_limits=request.rate_limits,
            expires_at=request.expires_at
        )
        
        self._keys[key_hash] = vk
        logger.info(f"Created new virtual key: {vk.id}")
        return raw_key, vk

    async def validate_key(self, raw_key: str) -> Optional[VirtualKey]:
        """Validate a raw API key and return the associated VirtualKey if valid."""
        key_hash = VirtualKey.hash_key(raw_key)
        
        if self.db:
            vk = await self.db.get_key_by_hash(key_hash)
        else:
            vk = self._keys.get(key_hash)
        
        if not vk or not vk.is_valid():
            # In dev mode, fall back to checking in-memory if we have a default dev key
            if not self.db and not vk:
                pass
            elif self.db and not vk:
                # Still check in-memory for the hardcoded dev key
                vk = self._keys.get(key_hash)
                if not vk or not vk.is_valid():
                    return None
            else:
                return None
            
        # Update last used timestamp
        vk.last_used_at = datetime.utcnow()
        if self.db and vk.id not in [v.id for v in self._keys.values()]:
            await self.db.update_key_last_used(vk.id)
            
        return vk

    async def revoke_key(self, key_id: UUID) -> bool:
        """Revoke a virtual key by ID."""
        if self.db:
            res = await self.db.revoke_key(key_id)
            if res:
                logger.info(f"Revoked virtual key in DB: {key_id}")
            return res
            
        for key_hash, vk in self._keys.items():
            if vk.id == key_id:
                vk.is_active = False
                logger.info(f"Revoked virtual key: {key_id}")
                return True
        return False

    async def list_keys(self) -> List[VirtualKey]:
        """List all virtual keys."""
        if self.db:
            return await self.db.list_keys()
        return list(self._keys.values())

    async def get_key_by_id(self, key_id: UUID) -> Optional[VirtualKey]:
        """Get a virtual key by ID."""
        if self.db:
            return await self.db.get_key_by_id(key_id)
            
        for vk in self._keys.values():
            if vk.id == key_id:
                return vk
        return None
