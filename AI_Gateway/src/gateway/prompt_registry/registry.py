"""
Prompt Registry.
Provides versioned prompt management. Agents can request a prompt template by name and version.
"""

import logging
from typing import Dict, Optional, List
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class PromptTemplate(BaseModel):
    name: str
    version: str
    system_prompt: str
    user_template: str
    is_active: bool = True

class PromptRegistry:
    """In-memory or DB-backed registry of approved prompts."""
    
    def __init__(self):
        # Maps name -> { version -> PromptTemplate }
        self._prompts: Dict[str, Dict[str, PromptTemplate]] = {}
        logger.info("Initialized PromptRegistry")
        
    def register_prompt(self, template: PromptTemplate) -> None:
        if template.name not in self._prompts:
            self._prompts[template.name] = {}
        self._prompts[template.name][template.version] = template
        logger.info(f"Registered prompt template: {template.name} v{template.version}")
        
    def get_prompt(self, name: str, version: str = "latest") -> Optional[PromptTemplate]:
        if name not in self._prompts:
            return None
            
        versions = self._prompts[name]
        
        if version == "latest":
            # Get the highest semver or simply the last added. Here we sort by string for simplicity.
            latest_ver = sorted(versions.keys(), reverse=True)[0]
            return versions[latest_ver]
            
        return versions.get(version)
        
    def list_prompts(self) -> List[PromptTemplate]:
        result = []
        for v_dict in self._prompts.values():
            result.extend(v_dict.values())
        return result
