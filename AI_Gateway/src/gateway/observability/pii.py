"""
PII (Personally Identifiable Information) Redaction.
Auto-redacts sensitive patterns from logs before they are written.
"""

import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PIIRedactor:
    """Scans and redacts PII from strings and dictionaries."""
    
    # Common regex patterns for PII
    PATTERNS = {
        "EMAIL": re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'),
        "CREDIT_CARD": re.compile(r'\b(?:\d[ -]*?){13,16}\b'),
        "SSN": re.compile(r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b'),
        "PHONE": re.compile(r'\b(?:\+?1[-. ]?)?\(?([0-9]{3})\)?[-. ]?([0-9]{3})[-. ]?([0-9]{4})\b'),
        # Logistics specific patterns can be added here
        "TRACKING_NUMBER": re.compile(r'\b(?:1Z[A-Z0-9]{16}|[0-9]{12,22})\b'),
    }

    def __init__(self, active: bool = True):
        self.active = active

    def redact_string(self, text: str) -> str:
        """Redacts PII from a single string."""
        if not self.active or not text:
            return text
            
        redacted_text = text
        for pii_type, pattern in self.PATTERNS.items():
            redacted_text = pattern.sub(f"[REDACTED {pii_type}]", redacted_text)
            
        return redacted_text

    def redact_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively redacts PII from all string values in a dictionary."""
        if not self.active or not data:
            return data
            
        redacted_data = {}
        for k, v in data.items():
            # Never redact keys, only values
            if isinstance(v, str):
                redacted_data[k] = self.redact_string(v)
            elif isinstance(v, dict):
                redacted_data[k] = self.redact_dict(v)
            elif isinstance(v, list):
                redacted_data[k] = [
                    self.redact_string(item) if isinstance(item, str) 
                    else (self.redact_dict(item) if isinstance(item, dict) else item)
                    for item in v
                ]
            else:
                redacted_data[k] = v
                
        return redacted_data
