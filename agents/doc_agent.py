"""
Doc Agent — generates README.md, API documentation, and setup guides.
Uses the same tier routing as other agents.
"""
from __future__ import annotations

from core.gemini import call_gemini_max, call_gemini_text

SYSTEM = """أنت خبير في كتابة توثيق المشاريع البرمجية.
مهمتك إنتاج README.md احترافية تشمل:
- وصف المشروع وما يفعله
- متطلبات التثبيت (Prerequisites)
- خطوات التشغيل بالتفصيل
- متغيرات البيئة المطلوبة (جدول)
- أمثلة الاستخدام
- بنية الملفات

اكتب بالعربية للشرح وبالإنجليزية للأوامر والكود.
النتيجة يجب أن تكون README.md كاملة وجاهزة للنشر."""


async def generate_readme(
    project_name: str,
    project_type: str,
    files: dict[str, str],
    user_inputs: dict[str, str] | None = None,
    tier: str = "x1.0",
) -> tuple[str, int]:
    """Generate a professional README.md for the project."""
    file_list = "\n".join(f"- {name}: {len(code)} chars" for name, code in files.items())
    env_vars = "\n".join(f"- {k}" for k in (user_inputs or {}).keys()) or "لا توجد"

    main_content = ""
    for priority in ["main.py", "bot.py", "app.py", "src/App.tsx", "app/index.tsx"]:
        if priority in files:
            main_content = f"\nملف رئيسي ({priority}):\n```\n{files[priority][:800]}\n```"
            break

    prompt = f"""أنشئ README.md احترافية لمشروع:

الاسم: {project_name}
النوع: {project_type}

الملفات المولّدة:
{file_list}

متغيرات البيئة:
{env_vars}
{main_content}

أنشئ README.md كاملة وجاهزة للنشر:"""

    if tier == "x2.0":
        return await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.3)
    return await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.3)


async def generate_api_docs(
    routes: list[dict],
    project_name: str,
    tier: str = "x1.0",
) -> tuple[str, int]:
    """Generate API documentation for FastAPI projects."""
    routes_text = "\n".join(
        f"- {r.get('method', 'GET')} {r.get('path', '/')} — {r.get('description', '')}"
        for r in routes
    )
    prompt = f"""وثّق هذه الـ API endpoints لمشروع FastAPI باسم {project_name}:

{routes_text}

أنتج Markdown احترافي يشمل:
- وصف كل endpoint
- المعطيات (Request body / Query params)
- أمثلة Request/Response بـ curl
- رموز الحالة (Status codes)"""

    if tier == "x2.0":
        return await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.2)
    return await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.2)
