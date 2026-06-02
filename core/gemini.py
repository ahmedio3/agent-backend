"""
Gemini client — three tiers for Veylor 2.0.

Tier        | Planning Agent        | All Other Agents
------------|----------------------|------------------
x1.0        | gemini-3.1-flash-lite | gemini-3.1-flash-lite
x1.5        | gemini-3.5-flash      | gemini-3.1-flash-lite
x2.0        | gemini-3.5-flash      | gemini-3.5-flash

Environment variables:
  GEMINI_KEYS        — keys for x1.0/x1.5 (comma-separated)
  VEYLOR_MAX_KEYS    — dedicated keys for x2.0 tier (comma-separated)
  GEMINI_MODEL       — override default lite model (default: gemini-3.1-flash-lite)
  GEMINI_MAX_MODEL   — override max/premium model (default: gemini-3.5-flash)
"""
from __future__ import annotations

import asyncio
import itertools
import os

from google import genai
from google.genai import types as gtypes

LITE_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
MAX_MODEL: str = os.getenv("GEMINI_MAX_MODEL", "gemini-3.5-flash")


# ── Key rotators ──────────────────────────────────────────────────────────────

class KeyRotator:
    def __init__(self, env_var: str) -> None:
        raw = os.getenv(env_var, "")
        self._keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not self._keys:
            raise RuntimeError(f"No keys found in {env_var}")
        self._cycle = itertools.cycle(self._keys)
        self._current = next(self._cycle)

    def current(self) -> str:
        return self._current

    def rotate(self) -> str:
        self._current = next(self._cycle)
        return self._current

    @property
    def total_keys(self) -> int:
        return len(self._keys)


_lite_rotator: KeyRotator | None = None
_max_rotator: KeyRotator | None = None


def get_lite_rotator() -> KeyRotator:
    global _lite_rotator
    if _lite_rotator is None:
        _lite_rotator = KeyRotator("GEMINI_KEYS")
    return _lite_rotator


def get_max_rotator() -> KeyRotator:
    global _max_rotator
    if _max_rotator is None:
        # VEYLOR_MAX_KEYS first, fall back to GEMINI_KEYS
        if os.getenv("VEYLOR_MAX_KEYS"):
            _max_rotator = KeyRotator("VEYLOR_MAX_KEYS")
        else:
            _max_rotator = KeyRotator("GEMINI_KEYS")
    return _max_rotator


# ── Core call logic ───────────────────────────────────────────────────────────

def _count_tokens(response) -> int:
    try:
        return int(response.usage_metadata.total_token_count or 0)
    except Exception:
        return 0


async def _call(
    *,
    prompt: str,
    system_instruction: str | None,
    model_name: str,
    temperature: float,
    rotator: KeyRotator,
    max_retries: int = 3,
    disable_thinking: bool = True,
) -> tuple[str, int]:
    total_attempts = max_retries * max(rotator.total_keys, 1)
    last_error: Exception | None = None

    for _ in range(total_attempts):
        key = rotator.current()
        client = genai.Client(api_key=key)
        try:
            config_kwargs: dict = dict(
                temperature=temperature,
                system_instruction=system_instruction,
            )
            if disable_thinking:
                config_kwargs["thinking_config"] = gtypes.ThinkingConfig(thinking_budget=0)

            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=gtypes.GenerateContentConfig(**config_kwargs),
            )
            return response.text or "", _count_tokens(response)

        except Exception as exc:
            last_error = exc
            msg = str(exc).lower()
            if any(x in msg for x in ("quota", "429", "rate_limit", "resource_exhausted")):
                rotator.rotate()
                await asyncio.sleep(1.0)
                continue
            if any(x in msg for x in ("permission_denied", "leaked", "403", "401")):
                rotator.rotate()
                continue
            if any(x in msg for x in ("not found", "does not exist", "404")):
                raise RuntimeError(f"Model '{model_name}' غير موجود.") from exc
            raise

    raise RuntimeError(
        f"All {rotator.total_keys} keys exhausted after {total_attempts} attempts. "
        f"Last: {last_error}"
    )


# ── Public API — three functions matching the three tiers ─────────────────────

async def call_gemini_text(
    *,
    prompt: str,
    system_instruction: str | None = None,
    model_name: str = LITE_MODEL,
    temperature: float = 0.4,
    max_retries: int = 3,
) -> tuple[str, int]:
    """x1.0 standard call — gemini-3.1-flash-lite, no thinking."""
    return await _call(
        prompt=prompt,
        system_instruction=system_instruction,
        model_name=model_name,
        temperature=temperature,
        rotator=get_lite_rotator(),
        max_retries=max_retries,
        disable_thinking=True,
    )


async def call_gemini_premium(
    *,
    prompt: str,
    system_instruction: str | None = None,
    model_name: str = MAX_MODEL,
    temperature: float = 0.4,
    max_retries: int = 2,
) -> tuple[str, int]:
    """x1.5 planning call — gemini-3.5-flash with Core rate limiter, falls back to lite."""
    from core.rate_limiter import get_core_limiter
    limiter = get_core_limiter()
    acquired = await limiter.try_acquire()
    if not acquired:
        return await call_gemini_text(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=temperature,
        )
    return await _call(
        prompt=prompt,
        system_instruction=system_instruction,
        model_name=model_name,
        temperature=temperature,
        rotator=get_lite_rotator(),
        max_retries=max_retries,
        disable_thinking=False,
    )


async def call_gemini_max(
    *,
    prompt: str,
    system_instruction: str | None = None,
    model_name: str = MAX_MODEL,
    temperature: float = 0.4,
) -> tuple[str, int]:
    """
    x2.0 tier call — gemini-3.5-flash with per-key rate limiting.
    - Respects local rate limits (5 RPM / 20 RPD per key).
    - Also catches Gemini API quota errors and marks the key exhausted,
      then retries with the next available key.
    - Raises RateLimitError if all keys are locally or remotely exhausted.
    """
    from core.rate_limiter import get_max_limiter
    limiter = get_max_limiter()
    n_keys = len(limiter._keys)

    for attempt in range(n_keys + 1):
        result = await limiter.acquire()
        if result is None:
            status = limiter.overall_status()
            if status == "day_exhausted":
                raise RateLimitError(
                    "تم استنفاد الحد اليومي لجميع مفاتيح Veylor x2.0 (20 طلب/يوم/مفتاح). "
                    "جرّب Veylor x1.5 أو x1.0، أو انتظر حتى إعادة التعيين.",
                    tier="x2.0", reason="day_exhausted",
                )
            raise RateLimitError(
                "تم الوصول للحد الأقصى في الدقيقة لجميع مفاتيح Veylor x2.0 (5 طلبات/دقيقة/مفتاح). "
                "انتظر دقيقة أو انتقل لـ Veylor x1.5.",
                tier="x2.0", reason="minute_limited",
            )

        api_key, key_idx = result
        client = genai.Client(api_key=api_key)
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=gtypes.GenerateContentConfig(
                    temperature=temperature,
                    system_instruction=system_instruction,
                ),
            )
            return response.text or "", _count_tokens(response)

        except Exception as exc:
            msg = str(exc).lower()
            is_quota = any(x in msg for x in (
                "quota", "429", "rate_limit", "resource_exhausted",
                "high demand", "overloaded", "too many requests",
            ))
            is_day_quota = any(x in msg for x in (
                "daily", "per day", "day limit", "exceeded your",
            ))

            if is_quota:
                key_limiter = limiter._limiters[key_idx]
                if is_day_quota:
                    key_limiter.force_mark_day_exhausted()
                else:
                    key_limiter.force_mark_minute_exhausted()
                continue

            raise

    raise RateLimitError(
        "جميع مفاتيح Veylor x2.0 تواجه ضغطًا عاليًا من Google API. "
        "جرّب Veylor x1.5 أو x1.0 للحصول على نتيجة فورية.",
        tier="x2.0", reason="api_quota_exhausted",
    )


class RateLimitError(Exception):
    def __init__(self, message: str, tier: str = "x2.0", reason: str = "") -> None:
        super().__init__(message)
        self.tier = tier
        self.reason = reason
