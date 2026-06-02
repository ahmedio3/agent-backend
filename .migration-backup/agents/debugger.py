"""Debugger agent — fixes code based on error logs."""
from __future__ import annotations

import re

from core.gemini import call_gemini_max, call_gemini_text

SYSTEM = """أنت خبير تصحيح أخطاء. مهمتك إصلاح الكود بناءً على رسالة الخطأ.
- حلّل الخطأ وحدد السبب الجذري
- أعد الكود المُصلح كاملاً (ملفات < 100 سطر) أو patch محدد (ملفات أكبر)
- اشرح سبب الخطأ بسطر واحد كتعليق"""


async def debug_file(
    file_path: str,
    current_code: str,
    error_log: str,
    attempt: int = 1,
    tier: str = "mini",
) -> tuple[str, str, int]:
    lines = current_code.count("\n")
    use_patch = lines > 80 and attempt > 1

    if use_patch:
        prompt = f"""ملف به خطأ: {file_path}

الكود:
```python
{current_code}
```

الخطأ:
```
{error_log}
```

أعد:
EXPLANATION: <سبب الخطأ>
OLD_SNIPPET:
```python
<الكود القديم>
```
NEW_SNIPPET:
```python
<الكود الجديد>
```"""
    else:
        prompt = f"""ملف به خطأ: {file_path}

الكود:
```python
{current_code}
```

الخطأ:
```
{error_log}
```

أعد الكود كاملاً مُصلحاً:"""

    if tier == "max":
        text, tokens = await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.1)
    else:
        text, tokens = await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.1)

    if use_patch:
        return "patch", text, tokens
    return "full", _extract_code(text), tokens


def _extract_code(text: str) -> str:
    m = re.findall(r"```(?:python)?\s*\n?(.*?)```", text, re.DOTALL)
    return m[0].strip() if m else text.strip()
