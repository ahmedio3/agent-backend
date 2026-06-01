"""
Maestro Orchestrator — drives the full agent loop with tier support.
tier: "mini" | "core" | "max"
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import AsyncGenerator

from agents.assistant import ask_assistant
from agents.coder import patch_code, write_file_code
from agents.debugger import debug_file as debug_agent
from agents.planner import plan as run_planner
from core.events import EventType, Timer, make_event
from core.gemini import RateLimitError
from core.project import Project
from tools.exec_tools import execute_command, install_requirements, syntax_check

MAX_STEPS = 15
MAX_DEBUG_ATTEMPTS = 3


async def run(
    prompt: str,
    user_inputs: dict[str, str] | None = None,
    tier: str = "mini",
) -> AsyncGenerator[str, None]:
    timer = Timer()
    total_tokens = 0
    project = Project()
    inputs = user_inputs or {}

    def evt(event_type: str, content: str, tokens: int = 0, **extra) -> str:
        return make_event(event_type, content=content,
                          time_ms=timer.elapsed_ms(), tokens_used=tokens, **extra)

    tier_label = {"mini": "🔵 Mini", "core": "🟡 Core", "max": "🔴 Max"}.get(tier, tier)
    yield evt(EventType.LOG, f"🚀 Vire 2.0 {tier_label} | مشروع: {project.project_id}")

    try:
        # ── STEP 1: Planner ───────────────────────────────────────────────────
        model_note = {
            "mini": "gemini-3.1-flash-lite",
            "core": "gemini-3.5-flash (planning)",
            "max": "gemini-3.5-flash (all)",
        }.get(tier, "")
        yield evt(EventType.TOOL_START, f"📋 المخطط يحلل طلبك ({model_note})...", tool="plan")
        t0 = time.perf_counter()

        try:
            plan_text, tok = await run_planner(prompt, inputs, tier=tier)
        except RateLimitError as e:
            yield evt(EventType.ERROR, str(e), error_type="RateLimitError",
                      tier=e.tier, reason=e.reason,
                      suggestion="switch_tier")
            return

        total_tokens += tok
        step_ms = int((time.perf_counter() - t0) * 1000)
        yield evt(EventType.PLAN, plan_text, tokens=tok, step_ms=step_ms)
        yield evt(EventType.TOOL_END, "✅ الخطة جاهزة", tokens=tok, tool="plan")
        project.log_step({"step": "plan", "ms": step_ms, "tokens": tok, "tier": tier})

        # ── STEP 2: Detect project type ───────────────────────────────────────
        project_type = _detect_project_type(plan_text, prompt)
        yield evt(EventType.LOG, f"🔍 نوع المشروع: {project_type}")

        # ── STEP 3: Detect required inputs ────────────────────────────────────
        missing = _detect_missing_inputs(plan_text, inputs)
        if missing:
            yield make_event(EventType.NEED_INPUT,
                             content="النظام يحتاج بعض المعلومات قبل المتابعة",
                             time_ms=timer.elapsed_ms(), tokens_used=0, fields=missing)
            return

        # ── STEP 4: File list ─────────────────────────────────────────────────
        files_plan = _extract_files_from_plan(plan_text)
        if not files_plan:
            yield evt(EventType.AGENT_THINKING, "🔍 تحديد الملفات المطلوبة...")
            files_raw, tok2 = await ask_assistant(
                "من الخطة، اكتب قائمة الملفات فقط (اسم كل ملف في سطر):\n" + plan_text,
                tier=tier,
            )
            total_tokens += tok2
            files_plan = _parse_file_list(files_raw)
        if not files_plan:
            files_plan = _default_files(project_type)

        yield evt(EventType.LOG, f"📄 الملفات: {', '.join(files_plan)}")

        # ── STEP 5: Code generation ───────────────────────────────────────────
        step_count = 2
        project_context = ""

        for file_path in files_plan:
            if step_count >= MAX_STEPS:
                yield evt(EventType.LOG, "⚠️ تجاوز الحد الأقصى للخطوات")
                break

            yield evt(EventType.TOOL_START, f"💻 كتابة: {file_path}", tool="write_file")
            t0 = time.perf_counter()

            try:
                code, tok = await write_file_code(
                    file_path=file_path,
                    description=_get_file_description(file_path, plan_text, project_type),
                    plan=plan_text,
                    project_context=project_context,
                    user_inputs=inputs,
                    project_type=project_type,
                    tier=tier,
                )
            except RateLimitError as e:
                yield evt(EventType.ERROR, str(e), error_type="RateLimitError",
                          tier=e.tier, reason=e.reason, suggestion="switch_tier")
                return

            total_tokens += tok
            step_ms = int((time.perf_counter() - t0) * 1000)
            project.write_file(file_path, code)
            project_context += f"\n\n--- {file_path} ---\n{code[:400]}..."

            yield evt(EventType.FILE_CREATED,
                      f"✅ {file_path} ({len(code)} حرف)",
                      tokens=tok, tool="write_file",
                      file_path=file_path, preview=code[:300], step_ms=step_ms)
            project.log_step({"step": "write_file", "file": file_path,
                              "ms": step_ms, "tokens": tok})
            step_count += 1

        # ── STEP 6: Install deps ──────────────────────────────────────────────
        if project_type in ("telegram", "fastapi") and project.file_exists("requirements.txt"):
            yield evt(EventType.TOOL_START, "📦 تثبيت المكتبات (pip)...", tool="execute_command")
            t0 = time.perf_counter()
            out, code = await install_requirements(project.root)
            step_ms = int((time.perf_counter() - t0) * 1000)
            yield evt(EventType.COMMAND_OUTPUT, out[:500] or "تم التثبيت",
                      tool="execute_command", command="pip install -r requirements.txt",
                      exit_code=code, step_ms=step_ms)
            step_count += 1

        elif project_type in ("react-web", "expo-mobile") and project.file_exists("package.json"):
            yield evt(EventType.TOOL_START, "📦 تثبيت المكتبات (npm)...", tool="execute_command")
            t0 = time.perf_counter()
            out, code = await execute_command("npm install --legacy-peer-deps", project.root)
            step_ms = int((time.perf_counter() - t0) * 1000)
            yield evt(EventType.COMMAND_OUTPUT, out[:500] or "تم التثبيت",
                      tool="execute_command", command="npm install",
                      exit_code=code, step_ms=step_ms)
            step_count += 1

            # Expo tunnel
            if project_type == "expo-mobile":
                import os as _os
                has_token = bool(_os.getenv("EXPO_TOKEN", ""))
                yield evt(EventType.TOOL_START,
                          "📱 تشغيل Expo tunnel..." + (" (مسجّل دخول)" if has_token else ""),
                          tool="expo_tunnel")
                from tools.exec_tools import start_expo_tunnel
                t0 = time.perf_counter()
                expo_url, _ = await start_expo_tunnel(project.root)
                step_ms = int((time.perf_counter() - t0) * 1000)
                if expo_url:
                    yield evt(EventType.TOOL_END,
                              f"✅ رابط Expo Go:\n`{expo_url}`\n\nافتح Expo Go ← Enter URL manually",
                              tool="expo_tunnel", step_ms=step_ms, expo_url=expo_url)
                else:
                    yield evt(EventType.LOG,
                              "⚠️ شغّل يدوياً: `npm install && npx expo start --tunnel`",
                              tool="expo_tunnel", step_ms=step_ms)
                step_count += 1

        # ── STEP 7: Validation (Python only) ──────────────────────────────────
        if project_type in ("telegram", "fastapi"):
            main_file = _detect_main_file(files_plan)
            if main_file and project.file_exists(main_file):
                yield evt(EventType.LOG, f"🔍 فحص الكود: {main_file}")
                debug_attempt = 0
                while debug_attempt < MAX_DEBUG_ATTEMPTS and step_count < MAX_STEPS:
                    ok, err = await syntax_check(project.root / main_file)
                    if ok:
                        yield evt(EventType.LOG, f"✅ الكود صحيح: {main_file}")
                        break

                    yield evt(EventType.DEBUG_START,
                              f"🐛 خطأ في {main_file} (محاولة {debug_attempt+1}/{MAX_DEBUG_ATTEMPTS})",
                              tool="debug_file", error=err[:300])
                    current_code = project.read_file(main_file)
                    t0 = time.perf_counter()
                    try:
                        fix_type, fix_content, tok = await debug_agent(
                            main_file, current_code, err,
                            attempt=debug_attempt + 1, tier=tier)
                    except RateLimitError as e:
                        yield evt(EventType.ERROR, str(e), error_type="RateLimitError",
                                  tier=e.tier, reason=e.reason)
                        break

                    total_tokens += tok
                    step_ms = int((time.perf_counter() - t0) * 1000)

                    if fix_type == "full":
                        project.write_file(main_file, fix_content)
                        yield evt(EventType.FILE_UPDATED, f"🔧 تم إصلاح {main_file}",
                                  tokens=tok, tool="debug_file",
                                  file_path=main_file, step_ms=step_ms)
                    else:
                        old, new, _ = _extract_patch_from_debug(fix_content)
                        if old and new:
                            try:
                                project.patch_file(main_file, old, new)
                                yield evt(EventType.FILE_UPDATED, f"🔧 تم تعديل {main_file}",
                                          tokens=tok, tool="debug_file",
                                          file_path=main_file, step_ms=step_ms)
                            except ValueError:
                                fixed, tok2 = await _force_full_rewrite(main_file, current_code, err, tier)
                                total_tokens += tok2
                                project.write_file(main_file, fixed)
                                yield evt(EventType.FILE_UPDATED,
                                          f"🔧 إعادة كتابة: {main_file}", tokens=tok2)

                    project.log_step({"step": "debug", "file": main_file,
                                     "attempt": debug_attempt + 1, "ms": step_ms, "tokens": tok})
                    step_count += 1
                    debug_attempt += 1

        # ── DONE ──────────────────────────────────────────────────────────────
        instructions = _build_run_instructions(project_type, files_plan)
        yield make_event(
            EventType.DONE,
            content=f"🎉 تم إنجاز المشروع! ({project_type} / {tier_label})\n\n{instructions}",
            time_ms=timer.elapsed_ms(), tokens_used=total_tokens,
            project_id=project.project_id,
            files=project.get_all_contents(),
            step_log=project.step_log,
            total_steps=step_count,
            project_type=project_type,
            tier=tier,
        )

    except Exception as exc:
        yield evt(EventType.ERROR, f"❌ خطأ غير متوقع: {exc}", error_type=type(exc).__name__)
        yield make_event(EventType.DONE, content="انتهى مع وجود أخطاء",
                         time_ms=timer.elapsed_ms(), tokens_used=total_tokens,
                         project_id=project.project_id,
                         files=project.get_all_contents(),
                         step_log=project.step_log, error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_project_type(plan_text: str, prompt: str) -> str:
    combined = (plan_text + " " + prompt).lower()
    if any(x in combined for x in ("expo", "react native", "expo-router", "موبايل")):
        return "expo-mobile"
    if any(x in combined for x in ("react", "vite", "web app", "تطبيق ويب", "material")):
        return "react-web"
    if any(x in combined for x in ("telegram", "تليجرام", "bot", "بوت")):
        return "telegram"
    return "fastapi"


_SENSITIVE_KEYWORDS = {
    "telegram_token": ["telegram", "bot token", "توكن", "تيليجرام"],
    "openai_api_key": ["openai", "gpt"],
    "gemini_api_key": ["gemini api", "google ai"],
    "anthropic_api_key": ["anthropic", "claude"],
    "custom_api_key": ["api key", "مفتاح api"],
    "webhook_url": ["webhook"],
    "database_url": ["database", "postgres", "mysql", "قاعدة بيانات"],
}

_FIELD_LABELS = {
    "telegram_token": {"label": "Telegram Bot Token", "hint": "أنشئ بوت عبر @BotFather", "type": "secret"},
    "openai_api_key": {"label": "OpenAI API Key", "hint": "من platform.openai.com", "type": "secret"},
    "gemini_api_key": {"label": "Gemini API Key", "hint": "من aistudio.google.com", "type": "secret"},
    "anthropic_api_key": {"label": "Anthropic API Key", "hint": "من console.anthropic.com", "type": "secret"},
    "custom_api_key": {"label": "API Key", "type": "secret"},
    "webhook_url": {"label": "Webhook URL", "type": "text"},
    "database_url": {"label": "Database URL", "hint": "postgresql://...", "type": "secret"},
}


def _detect_missing_inputs(plan_text: str, existing: dict[str, str]) -> list[dict]:
    plan_lower = plan_text.lower()
    missing = []
    for key, keywords in _SENSITIVE_KEYWORDS.items():
        if key in existing and existing[key]:
            continue
        if any(kw in plan_lower for kw in keywords):
            field = {"name": key, **_FIELD_LABELS.get(key, {"label": key, "type": "text"})}
            missing.append(field)
    return missing


def _default_files(project_type: str) -> list[str]:
    return {
        "telegram": ["bot.py", "requirements.txt"],
        "fastapi": ["main.py", "requirements.txt"],
        "react-web": ["package.json", "vite.config.ts", "index.html",
                      "src/main.tsx", "src/App.tsx", "src/theme.ts"],
        "expo-mobile": ["package.json", "app.json", "tsconfig.json",
                        "app/_layout.tsx", "app/index.tsx"],
    }.get(project_type, ["main.py", "requirements.txt"])


def _extract_files_from_plan(plan_text: str) -> list[str]:
    files: list[str] = []
    for pat in [r"`([a-zA-Z0-9_\-/]+\.[a-zA-Z]+)`",
                r"\*\*([a-zA-Z0-9_\-/]+\.[a-zA-Z]+)\*\*",
                r"(?:ملف|file)[:\s]+([a-zA-Z0-9_\-/]+\.[a-zA-Z]+)"]:
        for m in re.finditer(pat, plan_text, re.IGNORECASE):
            fname = m.group(1)
            if fname not in files and not fname.startswith(".") and len(fname) < 60:
                files.append(fname)
    return files


def _parse_file_list(raw: str) -> list[str]:
    return [
        line.strip().strip("-•*").strip()
        for line in raw.splitlines()
        if "." in line and " " not in line.strip() and len(line.strip()) < 60
    ]


def _get_file_description(file_path: str, plan_text: str, project_type: str = "auto") -> str:
    # 1. Try to extract description from the plan text itself
    for pat in [
        rf"(?:ملف|file)[:\s]*`?{re.escape(file_path)}`?[:\s-]+([^\n]+)",
        rf"`?{re.escape(file_path)}`?[:\s-]+([^\n]+)",
    ]:
        m = re.search(pat, plan_text, re.IGNORECASE)
        if m:
            return m.group(1).strip()

    # 2. Lookup by full file path — returns str, NOT nested dict
    path_descriptions: dict[str, str] = {
        "requirements.txt": "قائمة المكتبات Python",
        "package.json": f"قائمة dependencies للمشروع ({project_type})",
        "vite.config.ts": "إعدادات Vite + React",
        "vite.config.js": "إعدادات Vite",
        "index.html": "صفحة HTML الرئيسية",
        "tsconfig.json": "إعدادات TypeScript",
        "app.json": "إعدادات Expo",
        "theme.ts": "ثيم Material Design 3",
        ".env.example": "مثال على متغيرات البيئة",
        "Dockerfile": "ملف Docker للنشر",
        "docker-compose.yml": "إعدادات Docker Compose",
        "vercel.json": "إعدادات النشر على Vercel",
        "railway.toml": "إعدادات النشر على Railway",
    }
    if file_path in path_descriptions:
        return path_descriptions[file_path]

    # 3. Lookup by file stem (name without extension)
    name = Path(file_path).stem
    stem_descriptions: dict[str, str] = {
        "bot": "الكود الرئيسي لبوت تيليجرام",
        "main": "التطبيق الرئيسي",
        "app": "مكوّن التطبيق الرئيسي",
        "index": "نقطة الدخول الرئيسية",
        "layout": "تخطيط التطبيق (Layout)",
        "router": "إعدادات التوجيه (Router)",
        "store": "إدارة الحالة (State)",
        "api": "طبقة التواصل مع الـ API",
        "types": "تعريفات TypeScript",
        "utils": "دوال مساعدة",
        "config": "إعدادات المشروع",
        "schema": "مخطط قاعدة البيانات",
        "models": "نماذج البيانات",
    }
    return stem_descriptions.get(name, f"الكود الخاص بـ {file_path}")


def _detect_main_file(files: list[str]) -> str | None:
    for p in ["bot.py", "main.py", "app.py"]:
        if p in files:
            return p
    return next((f for f in files if f.endswith(".py")), None)


def _build_run_instructions(project_type: str, files: list[str]) -> str:
    return {
        "telegram": "```bash\npip install -r requirements.txt\npython bot.py\n```",
        "fastapi": "```bash\npip install -r requirements.txt\nuvicorn main:app --reload\n```",
        "react-web": "```bash\nnpm install\nnpm run dev\n```",
        "expo-mobile": "```bash\nnpm install\nnpx expo start --tunnel\n```\nثم افتح Expo Go وامسح الـ QR.",
    }.get(project_type, "")


def _extract_patch_from_debug(debug_output: str) -> tuple[str, str, str]:
    exp_m = re.search(r"EXPLANATION:\s*(.+?)(?:\n|$)", debug_output)
    old_m = re.search(r"OLD_SNIPPET:\s*```(?:\w+)?\s*\n?(.*?)```", debug_output, re.DOTALL)
    new_m = re.search(r"NEW_SNIPPET:\s*```(?:\w+)?\s*\n?(.*?)```", debug_output, re.DOTALL)
    return (old_m.group(1).strip() if old_m else ""), \
           (new_m.group(1).strip() if new_m else ""), \
           (exp_m.group(1).strip() if exp_m else "")


async def _force_full_rewrite(file_path: str, broken_code: str, error: str, tier: str) -> tuple[str, int]:
    from agents.debugger import debug_file as dbg
    _, fixed, tokens = await dbg(file_path, broken_code, error, attempt=99, tier=tier)
    return fixed, tokens
