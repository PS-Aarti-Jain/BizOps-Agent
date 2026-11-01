import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import (
    GROQ_API_KEY,
    GROQ_MAX_RETRIES,
    GROQ_MODEL,
    GROQ_TIMEOUT_SECONDS,
    LLM_TEMPERATURE,
)
from llm.base import LLMClient
from security.redaction import SecuritySanitizer

logger = logging.getLogger(__name__)


class GroqClient(LLMClient):
    """
    Official Groq API client supporting ultra-low-latency inference,
    JSON mode structured outputs, bounded retries, and comprehensive error handling.
    """
    provider_name: str = "groq"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = GROQ_TIMEOUT_SECONDS,
        max_retries: int = GROQ_MAX_RETRIES,
        temperature: float = LLM_TEMPERATURE,
        base_url: str = "https://api.groq.com/openai/v1",
    ):
        if api_key is not None:
            raw_key = api_key.strip()
        else:
            raw_key = (os.getenv("GROQ_API_KEY") or GROQ_API_KEY or "").strip()
        self.api_key = raw_key.lstrip("y") if raw_key.startswith("ygsk_") else raw_key
        self.model = (model or os.getenv("GROQ_MODEL") or GROQ_MODEL or "llama-3.3-70b-versatile").strip()
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.temperature = float(temperature)
        self.base_url = base_url.rstrip("/")

        self._cached_available: Optional[bool] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 60.0

        # Try initializing official Groq SDK client
        self._sdk_client: Any = None
        try:
            from groq import AsyncGroq
            if self.api_key:
                sdk_kwargs = {
                    "api_key": self.api_key,
                    "timeout": self.timeout,
                    "max_retries": self.max_retries,
                }
                if self.base_url and "api.groq.com/openai/v1" not in self.base_url:
                    sdk_kwargs["base_url"] = self.base_url
                self._sdk_client = AsyncGroq(**sdk_kwargs)
        except ImportError:
            logger.debug("groq SDK package not available; will use httpx directly.")

    @property
    def model_name(self) -> str:
        return self.model

    async def is_available(self) -> bool:
        """
        Verifies if the Groq API endpoint is reachable and the configured model exists.
        Caches availability for 60 seconds to avoid unnecessary network calls.
        """
        now = time.time()
        if self._cached_available is not None and (now - self._cache_timestamp) < self._cache_ttl:
            return self._cached_available

        if not self.api_key:
            self.last_status = "KEY_MISSING"
            self.last_error_message = (
                "GROQ_API_KEY is not set. Please configure GROQ_API_KEY in your .env file."
            )
            self._cached_available = False
            self._cache_timestamp = now
            return False

        if os.getenv("ENVIRONMENT") == "test" and not getattr(self, "_allow_live_network", False):
            self.last_status = "TEST_MOCKED"
            self.last_error_message = "Live network calls disabled in test environment."
            self._cached_available = False
            self._cache_timestamp = now
            return False

        url = f"{self.base_url}/models"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "BizOps-Agent/1.0",
        }

        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 5.0)) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    models = [m.get("id") for m in data.get("data", [])]
                    if self.model and self.model not in models:
                        candidates = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "allam-2-7b"]
                        found = next((c for c in candidates if c in models), None)
                        if found:
                            logger.info(f"Model '{self.model}' not in active model list. Auto-switching to '{found}'.")
                            self.model = found
                        else:
                            logger.warning(
                                f"Configured Groq model '{self.model}' not found in active model list. "
                                f"Available models: {models[:5]}..."
                            )
                    self.last_status = "ONLINE"
                    self.last_error_message = None
                    self._cached_available = True
                    self._cache_timestamp = now
                    return True
                elif res.status_code in (401, 403):
                    self.last_status = "UNAUTHORIZED"
                    self.last_error_message = (
                        f"Groq API authentication failed (HTTP {res.status_code}). Verify GROQ_API_KEY."
                    )
                    self._cached_available = False
                    self._cache_timestamp = now
                    return False
                else:
                    self.last_status = "SERVICE_UNAVAILABLE"
                    self.last_error_message = f"Groq /models returned HTTP {res.status_code}."
                    self._cached_available = False
                    self._cache_timestamp = now
                    return False
        except httpx.ConnectError:
            self.last_status = "SERVICE_UNAVAILABLE"
            self.last_error_message = f"Cannot connect to Groq API endpoint at {self.base_url}."
            self._cached_available = False
            self._cache_timestamp = now
            return False
        except httpx.TimeoutException:
            self.last_status = "TIMEOUT"
            self.last_error_message = f"Groq API connection timed out while checking models."
            self._cached_available = False
            self._cache_timestamp = now
            return False
        except Exception as e:
            self.last_status = "SERVICE_UNAVAILABLE"
            self.last_error_message = SecuritySanitizer.mask_secrets(
                f"Groq API check failed: {str(e)}", self.api_key
            )
            self._cached_available = False
            self._cache_timestamp = now
            return False

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Generates structured JSON via Groq chat completions API with JSON object response format.
        """
        if not self.api_key:
            self.last_status = "KEY_MISSING"
            self.last_error_message = "Cannot call Groq API: GROQ_API_KEY is not configured."
            return None

        # Prefer official SDK if client is initialized
        if self._sdk_client:
            return await self._generate_via_sdk(prompt, system_prompt, tools)

        return await self._generate_via_httpx(prompt, system_prompt, tools)

    async def _generate_via_sdk(
        self,
        prompt: str,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        t0 = time.time()
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._sdk_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                )
                self.last_latency_ms = round((time.time() - t0) * 1000, 2)
                if response.choices:
                    content = response.choices[0].message.content or ""
                    parsed = self._clean_and_parse_json(content)
                    if parsed is not None:
                        self.last_status = "SUCCESS"
                        self.last_error_message = None
                        return parsed
                self.last_status = "MALFORMED_RESPONSE"
                self.last_error_message = "Groq returned content that could not be parsed as JSON."
                return None
            except Exception as e:
                err_str = str(e)
                logger.warning(
                    f"Groq SDK call attempt {attempt + 1}/{self.max_retries + 1} failed: {SecuritySanitizer.mask_secrets(err_str, self.api_key)}"
                )
                if attempt == self.max_retries:
                    self.last_status = "ERROR"
                    self.last_error_message = SecuritySanitizer.mask_secrets(err_str, self.api_key)
                    return None
                await asyncio.sleep(0.5)
        return None

    async def _generate_via_httpx(
        self,
        prompt: str,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "BizOps-Agent/1.0",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }

        t0 = time.time()
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    res = await client.post(url, headers=headers, json=payload)
                    self.last_latency_ms = round((time.time() - t0) * 1000, 2)

                    if res.status_code == 200:
                        data = res.json()
                        choices = data.get("choices", [])
                        if not choices:
                            self.last_status = "EMPTY_RESPONSE"
                            self.last_error_message = "Groq returned 200 OK but choices array is empty."
                            return None

                        content = choices[0].get("message", {}).get("content", "")
                        parsed = self._clean_and_parse_json(content)
                        if parsed is not None:
                            self.last_status = "SUCCESS"
                            self.last_error_message = None
                            return parsed
                        else:
                            self.last_status = "MALFORMED_RESPONSE"
                            self.last_error_message = "Groq returned content that could not be parsed as JSON."
                            return None
                    elif res.status_code == 429:
                        self.last_status = "RATE_LIMIT"
                        self.last_error_message = "Groq API rate limit reached. Waiting to retry."
                        if attempt < self.max_retries:
                            await asyncio.sleep(1.0)
                            continue
                        return None
                    elif res.status_code in (401, 403):
                        self.last_status = "UNAUTHORIZED"
                        self.last_error_message = f"Groq API authentication failed (HTTP {res.status_code})."
                        return None
                    else:
                        self.last_status = "SERVICE_ERROR"
                        self.last_error_message = f"Groq API returned HTTP {res.status_code}: {res.text[:200]}"
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5)
                            continue
                        return None
            except httpx.TimeoutException:
                self.last_status = "TIMEOUT"
                self.last_error_message = f"Groq API request timed out after {self.timeout}s."
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5)
                    continue
                return None
            except Exception as e:
                self.last_status = "SERVICE_ERROR"
                self.last_error_message = SecuritySanitizer.mask_secrets(str(e), self.api_key)
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5)
                    continue
                return None
        return None

    async def close(self) -> None:
        if self._sdk_client and hasattr(self._sdk_client, "close"):
            try:
                await self._sdk_client.close()
            except Exception:
                pass

