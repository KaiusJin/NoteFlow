import re


CASES_GLYPH_RUN_RE = re.compile(r"[\uf8f1\uf8f2\uf8f3\uf8f4]+")
LEFT_PAREN_GLYPH_RUN_RE = re.compile(r"[\uf8eb\uf8ec\uf8ed]+")
RIGHT_PAREN_GLYPH_RUN_RE = re.compile(r"[\uf8f6\uf8f7\uf8f8]+")

CONTROL_CHAR_REPLACEMENTS = {
    "\x10": "(",
    "\x11": ")",
    "\x12": "",
    "\x13": "",
}


def normalize_pdf_math_text(text: str) -> str:
    if not text:
        return ""
    normalized = text
    for source, replacement in CONTROL_CHAR_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    normalized = CASES_GLYPH_RUN_RE.sub("\n\\\\begin{cases}\n", normalized)
    normalized = LEFT_PAREN_GLYPH_RUN_RE.sub("(", normalized)
    normalized = RIGHT_PAREN_GLYPH_RUN_RE.sub(")", normalized)
    normalized = remove_unsupported_control_chars(normalized)
    normalized = cleanup_cases_markers(normalized)
    normalized = normalize_math_spacing(normalized)
    return normalized.strip()


def remove_unsupported_control_chars(text: str) -> str:
    return "".join(
        char
        for char in text
        if char in {"\n", "\t"} or ord(char) >= 32
    )


def cleanup_cases_markers(text: str) -> str:
    text = re.sub(r"(?:\s*\\begin\{cases\}\s*)+", lambda _: "\n\\begin{cases}\n", text)
    text = re.sub(r"(?:\s*\\end\{cases\}\s*)+", lambda _: "\n\\end{cases}\n", text)
    text = re.sub(r"\\begin\{cases\}\s*\\end\{cases\}", "", text)
    return text


def balance_cases_environment(text: str) -> str:
    begins = text.count("\\begin{cases}")
    ends = text.count("\\end{cases}")
    if begins <= ends:
        return text
    return text.rstrip() + ("\n\\end{cases}" * (begins - ends))


def normalize_math_spacing(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = re.sub(r"[ \t]+", " ", line).strip()
        if stripped:
            lines.append(stripped)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()
