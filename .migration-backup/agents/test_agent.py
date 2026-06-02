"""
Test Agent — generates unit tests and integration tests.
"""
from __future__ import annotations

from core.gemini import call_gemini_max, call_gemini_text

SYSTEM = """أنت خبير في كتابة الاختبارات البرمجية.

لـ Python: استخدم pytest مع fixtures واضحة. كود اختبار كامل.
لـ TypeScript/React: استخدم Vitest + @testing-library/react. لا mock غير ضروري.
لـ Expo/React Native: استخدم Jest + @testing-library/react-native.

القواعد:
- اختبر السلوك، لا التطبيق
- اسم كل اختبار يصف ما يفعله
- كود قابل للتشغيل مباشرة
- ضع الكود داخل code block"""


async def generate_tests(
    file_path: str,
    file_content: str,
    project_type: str = "fastapi",
    tier: str = "mini",
) -> tuple[str, int]:
    """Generate test file for a given source file."""
    lang = "python" if project_type in ("fastapi", "telegram") else "typescript"
    test_framework = {
        "fastapi": "pytest + httpx (async client for FastAPI)",
        "telegram": "pytest + unittest.mock",
        "react-web": "Vitest + @testing-library/react",
        "expo-mobile": "Jest + @testing-library/react-native",
    }.get(project_type, "pytest")

    prompt = f"""اكتب اختبارات لـ {file_path} باستخدام {test_framework}:

```{lang}
{file_content[:1500]}
```

اكتب ملف اختبار كاملاً يغطي:
- الحالات الطبيعية (happy path)
- حالات الخطأ والـ edge cases
- Mock للـ external dependencies"""

    if tier == "max":
        text, tokens = await call_gemini_max(prompt=prompt, system_instruction=SYSTEM, temperature=0.2)
    else:
        text, tokens = await call_gemini_text(prompt=prompt, system_instruction=SYSTEM, temperature=0.2)
    return text, tokens


async def generate_test_suite(
    files: dict[str, str],
    project_type: str,
    tier: str = "mini",
) -> dict[str, tuple[str, int]]:
    """Generate tests for all main source files."""
    results = {}
    testable = {
        "fastapi": [f for f in files if f.endswith(".py") and "test" not in f],
        "telegram": [f for f in files if f.endswith(".py") and "test" not in f],
        "react-web": [f for f in files if f.endswith((".ts", ".tsx")) and "test" not in f and "config" not in f],
        "expo-mobile": [f for f in files if f.endswith((".ts", ".tsx")) and "test" not in f],
    }.get(project_type, [])

    for file_path in testable[:3]:  # Max 3 files to avoid timeout
        content = files.get(file_path, "")
        if len(content) < 50:
            continue
        test_path = _get_test_path(file_path, project_type)
        text, tokens = await generate_tests(file_path, content, project_type, tier)
        results[test_path] = (text, tokens)
    return results


def _get_test_path(file_path: str, project_type: str) -> str:
    name = file_path.rsplit(".", 1)[0].replace("/", "_").replace("src_", "")
    if project_type in ("fastapi", "telegram"):
        return f"tests/test_{name}.py"
    return f"src/__tests__/{name}.test.ts"
