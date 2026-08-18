"""Filename generation from naming templates."""

import re
from datetime import datetime

TOKENS = ["{original_name}", "{range}", "{start_page}", "{end_page}", "{timestamp}"]


def sanitize(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" .")
    return name or "document"


def apply_template(pattern: str, original_name: str, start_page: int, end_page: int, fmt: str = "") -> str:
    base = original_name
    if base.lower().endswith((".docx", ".doc", ".docm", ".dotx", ".dotm", ".rtf", ".pdf")):
        base = base[: base.rfind(".")]
    name = sanitize(base)

    preview = pattern.replace("{original_name}", name)
    preview = preview.replace("{range}", f"{start_page}-{end_page}")
    preview = preview.replace("{start_page}", str(start_page))
    preview = preview.replace("{end_page}", str(end_page))
    preview = preview.replace("{timestamp}", datetime.now().strftime("%Y%m%d_%H%M%S"))
    preview = sanitize(preview) if preview else f"{name}_pages_{start_page}-{end_page}"

    if fmt:
        ext = fmt.lower().lstrip(".")
        if ext == "same":
            return preview
        return preview + "." + ext
    return preview
