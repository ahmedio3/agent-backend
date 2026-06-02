"""
Planner agent.
- x1.0 tier  → gemini-3.1-flash-lite
- x1.5 tier  → gemini-3.5-flash (premium, falls back to lite if rate-limited)
- x2.0 tier  → gemini-3.5-flash (per-key rate limited, raises RateLimitError)
"""
from __future__ import annotations

from core.gemini import call_gemini_max, call_gemini_premium, call_gemini_text

SYSTEM = """أنت وكيل تخطيط متخصص في بناء مشاريع برمجية من أربعة أنواع:

1. **بوتات تليجرام** — python-telegram-bot (async, v20+)
2. **تطبيقات FastAPI** — Python REST API مع uvicorn
3. **تطبيقات ويب React** — React 18 + TypeScript + Vite + Material Design 3 (mobile-first)
4. **تطبيقات موبايل Expo** — Expo SDK + React Native + TypeScript (mobile-first)

مهمتك: أنتج خطة عمل مفصّلة تتضمن:
- **نوع المشروع** (تليجرام / FastAPI / React-Web / Expo-Mobile)
- **قائمة الملفات المطلوبة** مع وصف محتوى كل ملف
- **المكتبات المطلوبة** (requirements.txt أو package.json)
- **متغيرات البيئة** المطلوبة
- **ترتيب خطوات التنفيذ**

لمشاريع React: Material Design 3 عبر @mui/material v6، mobile-first، TypeScript صارم.
لمشاريع Expo: expo-router، React Native Paper، TypeScript.

أجب بصيغة منظمة واضحة. لا تكتب أي كود."""


async def plan(
    description: str,
    user_inputs: dict[str, str] | None = None,
    tier: str = "x1.0",
) -> tuple[str, int]:
    """Generate a structured plan. Returns (plan_text, tokens_used)."""
    inputs_note = ""
    if user_inputs:
        inputs_note = "\n\nالمدخلات المتوفرة:\n" + "\n".join(
            f"- {k}: {'[متوفر]' if v else '[غير متوفر]'}" for k, v in user_inputs.items()
        )

    prompt = f"""وصف المشروع:
{description}{inputs_note}

ضع خطة عمل مفصّلة خطوة بخطوة."""

    if tier == "x2.0":
        return await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.3)
    elif tier == "x1.5":
        return await call_gemini_premium(prompt=prompt, system_instruction=SYSTEM, temperature=0.3)
    else:
        return await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.3)
