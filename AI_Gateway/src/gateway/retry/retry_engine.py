"""Retry engine with exponential backoff and jitter."""

import logging
import asyncio
import random
from typing import Callable, Any
import httpx

logger = logging.getLogger(__name__)

class RetryEngine:
    """Executes provider calls with retry logic."""
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def execute_with_retry(self, func: Callable, *args, **kwargs) -> Any:
        retries = 0
        while True:
            try:
                return await func(*args, **kwargs)
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                # Don't retry on client errors (4xx) except 429
                if isinstance(e, httpx.HTTPStatusError):
                    status_code = e.response.status_code
                    if 400 <= status_code < 500 and status_code != 429:
                        raise e

                retries += 1
                if retries > self.max_retries:
                    logger.error(f"Max retries ({self.max_retries}) exceeded.")
                    raise e
                    
                delay = min(self.max_delay, self.base_delay * (2 ** (retries - 1)))
                jitter = random.uniform(0, 0.1 * delay)
                sleep_time = delay + jitter
                
                logger.warning(f"Request failed, retrying in {sleep_time:.2f}s... (Attempt {retries}/{self.max_retries})")
                await asyncio.sleep(sleep_time)
            except Exception as e:
                # Other exceptions
                raise e
