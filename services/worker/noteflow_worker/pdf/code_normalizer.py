import re
import textwrap


def detect_code_language(text: str) -> str:
    lowered = text.lower()
    if re.search(r"^\s*(select|insert|update|delete|create)\b", text, re.IGNORECASE | re.MULTILINE):
        return "sql"
    if re.search(r"^\s*\((define|lambda|let\*?|cond)\b", text, re.MULTILINE):
        return "scheme"
    if re.search(r"^\s*(async\s+def|def|from\s+\S+\s+import|import\s+\S+)", text, re.MULTILINE):
        return "python"
    if "console.log(" in text or "=>" in text or re.search(r"^\s*(const|let|var)\s+\w+", text, re.MULTILINE):
        return "javascript"
    if "system.out." in lowered or "public static" in lowered or re.search(r"\bpublic\s+class\b", lowered):
        return "java"
    if "#include" in text or "std::" in text:
        return "cpp" if "std::" in text or "using namespace" in lowered else "c"
    if "<- function(" in text or re.search(r"\bfunction\s*\([^)]*\)\s*\{", text):
        return "r"
    if text.lstrip().startswith("#!") or re.search(r"^\s*(echo|export|set -[a-z])\b", text, re.MULTILINE):
        return "bash"
    return "text"


def normalize_code_source(text: str) -> str:
    lines = [line.rstrip().replace("\x00", "") for line in text.expandtabs(4).splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return textwrap.dedent("\n".join(lines)).rstrip()
