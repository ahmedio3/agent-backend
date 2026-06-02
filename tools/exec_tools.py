"""Execute shell commands inside the isolated project directory."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path


async def execute_command(
    command: str,
    cwd: Path,
    timeout: int = 30,
    extra_env: dict | None = None,
) -> tuple[str, int]:
    """Run a shell command inside cwd. Returns (combined_output, return_code)."""
    env = {**os.environ}
    env["PIP_TARGET"] = str(cwd / ".pip_packages")
    env["PYTHONPATH"] = str(cwd / ".pip_packages") + ":" + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode("utf-8", errors="replace"), proc.returncode or 0
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"[timeout after {timeout}s]", 1
    except Exception as exc:
        return f"[error: {exc}]", 1


async def install_requirements(cwd: Path) -> tuple[str, int]:
    """pip install -r requirements.txt into .pip_packages."""
    if not (cwd / "requirements.txt").exists():
        return "No requirements.txt found", 0
    pip_cmd = f"{sys.executable} -m pip install -r requirements.txt --target .pip_packages -q"
    return await execute_command(pip_cmd, cwd, timeout=120)


async def syntax_check(file_path: Path) -> tuple[bool, str]:
    """Quick Python syntax check."""
    cmd = f"{sys.executable} -m py_compile {file_path.name}"
    output, code = await execute_command(cmd, file_path.parent, timeout=10)
    return code == 0, output


async def run_python_file(file_path: Path, timeout: int = 10) -> tuple[str, int]:
    """Run a Python file and capture output."""
    cmd = f"{sys.executable} {file_path.name}"
    return await execute_command(cmd, file_path.parent, timeout=timeout)


async def check_node_available() -> bool:
    """Check if Node.js and npx are available in this environment."""
    out, code = await execute_command("node --version", Path("/tmp"), timeout=5)
    return code == 0 and out.strip().startswith("v")


async def start_expo_tunnel(cwd: Path) -> tuple[str, str]:
    """
    Attempt to start Expo tunnel and capture the exp:// URL.

    IMPORTANT: This only works in environments where:
    1. Node.js + npx are available
    2. @expo/ngrok is installable
    3. EXPO_TOKEN is set (for authentication)

    On Railway/cloud deployments, this typically CANNOT work because:
    - The container doesn't have Node.js or Expo CLI
    - ngrok requires auth and specific binary versions
    - The tunnel URL would point to an ephemeral cloud container

    When it fails, the orchestrator uses the download endpoint instead.
    Returns (expo_url_or_empty, raw_output_for_logging).
    """
    # Step 1: Check if Node is available at all
    node_ok = await check_node_available()
    if not node_ok:
        return "", "[Node.js not available in this environment — download ZIP to run locally]"

    expo_token = os.getenv("EXPO_TOKEN", "")
    extra_env: dict[str, str] = {
        "CI": "0",
        "EXPO_NO_DOTENV": "1",
        "NODE_NO_WARNINGS": "1",
    }
    if expo_token:
        extra_env["EXPO_TOKEN"] = expo_token

    env = {**os.environ, **extra_env}

    # Step 2: Ensure @expo/ngrok is installed locally in the project
    ngrok_check, _ = await execute_command(
        "npx --no -- expo --version", cwd, timeout=15
    )

    try:
        proc = await asyncio.create_subprocess_shell(
            "npx expo start --tunnel --non-interactive",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=env,
        )
    except Exception as exc:
        return "", f"Failed to start expo process: {exc}"

    url = ""
    collected: list[str] = []

    try:
        assert proc.stdout is not None
        deadline = asyncio.get_event_loop().time() + 90  # 90s max wait
        while asyncio.get_event_loop().time() < deadline:
            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            except asyncio.TimeoutError:
                continue
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            collected.append(line)
            # Match exp:// or exp+scheme:// URLs
            m = re.search(r"exp(?:\+[a-z]+)?://[\w\-.:/@?=&%]+", line)
            if m:
                url = m.group(0).rstrip(").,")
                break
            # Detect failure early
            if any(x in line.lower() for x in ("error", "failed", "enoent")):
                collected.append(f"[early failure detected]: {line}")
                if "ngrok" in line.lower() or "tunnel" in line.lower():
                    break
    finally:
        try:
            proc.kill()
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            pass

    raw = "\n".join(collected[-20:])
    return url, raw
