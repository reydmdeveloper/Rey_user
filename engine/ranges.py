"""Parsing of page-range specifications such as '1-4,5-6,7-10', '1-end', 'even', 'odd', 'all-individual'."""

import re

TOKEN = re.compile(r"(\d+)\s*-\s*(\d+|\bend\b)|\bend\b|\beven\b|\bodd\b|\ball(-individual)?\b|\b(?:even|odd)\.?\b|(\d+)")


def _expand(spec: str, total_pages: int):
    spec = spec.strip().lower()
    if not spec:
        raise ValueError("Range spec is empty.")

    if spec in ("all", "all pages", "1-end", "1 - end"):
        return [(1, total_pages)]

    if spec == "all-individual":
        return [(p, p) for p in range(1, total_pages + 1)]

    if spec == "even":
        return [(p, p) for p in range(2, total_pages + 1, 2)]

    if spec == "odd":
        return [(p, p) for p in range(1, total_pages + 1, 2)]

    # Comma separated tokens: "1-4", "5-6", "7-10", "1", "3-end", "end"
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    ranges = []
    for part in parts:
        m = re.fullmatch(r"(\d+)\s*-\s*(.*)", part)
        if m:
            start = int(m.group(1))
            end_token = m.group(2).strip()
            if end_token in ("end", ""):
                end = total_pages
            else:
                end = int(end_token)
            if start < 1:
                raise ValueError(f"Invalid range start: {start}")
            if end > total_pages:
                end = total_pages
            if end < start:
                raise ValueError(f"Invalid range '{part}': end page {end} is before start page {start}.")
            ranges.append((start, end))
            continue
        m = re.fullmatch(r"(\d+)", part)
        if m:
            p = int(m.group(1))
            if p < 1 or p > total_pages:
                raise ValueError(f"Page {p} is out of range (document has {total_pages} pages).")
            ranges.append((p, p))
            continue
        if part == "end":
            ranges.append((total_pages, total_pages))
            continue
        raise ValueError(f"Unrecognized range token: '{part}'")

    if not ranges:
        raise ValueError(f"Could not parse range spec: '{spec}'")
    return ranges


def parse_range_spec(spec: str, total_pages: int):
    """Return a list of (start, end) 1-indexed inclusive page ranges, deduplicated and sorted."""
    ranges = _expand(spec, total_pages)
    seen = set()
    out = []
    for start, end in ranges:
        for p in range(start, end + 1):
            if p not in seen:
                seen.add(p)
        key = (start, end)
        if key not in out:
            out.append(key)
    out.sort()
    return out
