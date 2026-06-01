"""Coder agent — writes code for all supported project types."""
from __future__ import annotations

import re

from core.gemini import call_gemini_max, call_gemini_text

SYSTEM = """أنت مبرمج متخصص في أربعة أنواع من المشاريع:

### 1. بوتات تليجرام (Python)
- python-telegram-bot v20+ مع async/await، متغيرات بيئة بـ os.getenv

### 2. تطبيقات FastAPI (Python)
- uvicorn، Pydantic v2

### 3. تطبيقات React Web (TypeScript)
- React 18 + TypeScript + Vite، Material Design 3 (@mui/material v6)
- mobile-first (useMediaQuery, sx prop)، functional components + hooks فقط

### 4. تطبيقات Expo Mobile (TypeScript)
- Expo SDK 51+ مع expo-router، React Native Paper لـ Material 3
- StyleSheet.create لجميع الـ styles

قواعد:
- كود كامل وجاهز للتشغيل فوراً
- الكود داخل code block: python / typescript / json / html
- لا شرح خارج الـ code block"""


async def write_file_code(
    file_path: str,
    description: str,
    plan: str,
    project_context: str = "",
    user_inputs: dict[str, str] | None = None,
    project_type: str = "auto",
    tier: str = "mini",
) -> tuple[str, int]:
    inputs_section = ""
    if user_inputs:
        inputs_section = "\nالمدخلات:\n" + "\n".join(
            f"- {k} = {v!r}" for k, v in user_inputs.items()
        )
    context_section = f"\nسياق:\n{project_context}" if project_context else ""
    type_hint = f"\nنوع المشروع: {project_type}" if project_type != "auto" else ""

    prompt = f"""اكتب الكود الكامل للملف: {file_path}{type_hint}

الخطة:
{plan}

المطلوب من هذا الملف:
{description}{inputs_section}{context_section}

اكتب الكود الآن:"""

    if tier == "max":
        text, tokens = await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.2)
    else:
        text, tokens = await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.2)
    return _extract_code(text, file_path), tokens


async def patch_code(
    file_path: str,
    current_code: str,
    change_description: str,
    tier: str = "mini",
) -> tuple[str, str, int]:
    lang = _detect_lang(file_path)
    prompt = f"""لديك الملف: {file_path}

الكود:
```{lang}
{current_code}
```

التعديل المطلوب: {change_description}

أعد:
OLD_SNIPPET:
```{lang}
<الكود القديم>
```
NEW_SNIPPET:
```{lang}
<الكود الجديد>
```"""

    if tier == "max":
        text, tokens = await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.1)
    else:
        text, tokens = await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.1)
    old, new = _extract_patch(text, lang)
    return old, new, tokens


def _detect_lang(file_path: str) -> str:
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return {"py": "python", "ts": "typescript", "tsx": "typescript",
            "js": "javascript", "jsx": "javascript", "json": "json",
            "html": "html", "css": "css"}.get(ext, "python")


def _extract_code(text: str, file_path: str = "") -> str:
    lang = _detect_lang(file_path)
    for pat in [
        rf"```(?:{lang})\s*\n?(.*?)```",
        r"```(?:python|typescript|javascript|ts|tsx|js|jsx|json|html|css)\s*\n?(.*?)```",
        r"```\s*\n?(.*?)```",
    ]:
        m = re.findall(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            return m[0].strip()
    return text.strip()


def _extract_patch(text: str, lang: str = "python") -> tuple[str, str]:
    old_m = re.search(rf"OLD_SNIPPET:\s*```(?:{lang})?\s*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    new_m = re.search(rf"NEW_SNIPPET:\s*```(?:{lang})?\s*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (old_m.group(1).strip() if old_m else ""), (new_m.group(1).strip() if new_m else "")
