"""Assistant agent — quick sub-tasks for the orchestrator."""
from __future__ import annotations

from core.gemini import call_gemini_max, call_gemini_text

SYSTEM = """أنت مساعد برمجي سريع.
- إذا طُلب كود: اكتبه داخل ```python ... ```
- إذا طُلب شرح: أجب بإيجاز بالعربية
- إذا طُلبت قائمة مكتبات: اكتبها بصيغة requirements.txt"""


async def ask_assistant(
    task: str,
    context: str = "",
    tier: str = "x1.0",
) -> tuple[str, int]:
    prompt = f"السياق:\n{context}\n\nالمهمة:\n{task}" if context else task

    if tier == "x2.0":
        return await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.4)
    return await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.4)
