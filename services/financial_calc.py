"""
Financial Calculator — programmatic number extraction and comparison.

Used when boost_tables=True in the query route. Extracts numbers from
table_json chunks and computes deltas/percentages so the LLM doesn't
have to do arithmetic.

API-free: pure Python computation.
"""

import json
import re
import logging
from typing import List, Dict, Optional, Any

log = logging.getLogger(__name__)


def parse_dutch_number(text: str) -> Optional[float]:
    """
    Parse a Dutch-formatted number string to float.
    Handles: '1.234', '1.234,56', '1234', '-1.234', '€ 1.234', '1,5 mln'
    """
    if not text or not isinstance(text, str):
        return None
    clean = text.strip()
    # Remove currency symbols and whitespace
    clean = re.sub(r'[€$\s]', '', clean)
    # Remove trailing % or units
    clean = re.sub(r'[%]$', '', clean)

    # Handle 'mln' / 'miljoen' / 'mrd' / 'miljard'
    multiplier = 1.0
    if re.search(r'ml[nj]|miljoen', clean, re.IGNORECASE):
        multiplier = 1_000_000
        clean = re.sub(r'\s*(ml[nj]|miljoen).*', '', clean, flags=re.IGNORECASE)
    elif re.search(r'mrd|miljard', clean, re.IGNORECASE):
        multiplier = 1_000_000_000
        clean = re.sub(r'\s*(mrd|miljard).*', '', clean, flags=re.IGNORECASE)

    # Dutch format: 1.234,56 → 1234.56
    if ',' in clean and '.' in clean:
        # Both present: dot is thousand sep, comma is decimal
        clean = clean.replace('.', '').replace(',', '.')
    elif ',' in clean:
        # Comma only: decimal separator
        clean = clean.replace(',', '.')
    elif '.' in clean:
        # Dot only: check if it's a thousand separator (3 digits after dot)
        # e.g. "1.234" → 1234, but "1.5" → 1.5
        parts = clean.split('.')
        if len(parts) == 2 and len(parts[1]) == 3:
            clean = clean.replace('.', '')
        elif len(parts) > 2:
            # Multiple dots = thousand separators: 1.234.567
            clean = clean.replace('.', '')

    try:
        return float(clean) * multiplier
    except (ValueError, TypeError):
        return None


def extract_table_numbers(table_json: str) -> List[Dict[str, Any]]:
    """
    Extract labelled numbers from a table_json string.
    Returns list of {"label": str, "year": str|None, "value": float, "raw": str}.
    """
    try:
        data = json.loads(table_json) if isinstance(table_json, str) else table_json
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, dict) or 'headers' not in data or 'rows' not in data:
        return []

    headers = data['headers']
    rows = data['rows']
    results = []

    # Identify year columns (headers that look like years: 2018, 2019, etc.)
    year_cols = {}
    for i, h in enumerate(headers):
        h_clean = str(h).strip()
        if re.match(r'^(19|20)\d{2}', h_clean):
            year_cols[i] = h_clean[:4]

    for row in rows:
        if not row or len(row) < 2:
            continue
        label = str(row[0]).strip() if row[0] else ""
        if not label or label.lower() in ('', 'totaal generaal'):
            continue

        # Extract numbers from year columns
        for col_idx, year in year_cols.items():
            if col_idx < len(row):
                val = parse_dutch_number(str(row[col_idx]))
                if val is not None and val != 0:
                    results.append({
                        "label": label,
                        "year": year,
                        "value": val,
                        "raw": str(row[col_idx]).strip(),
                    })

        # Also extract from non-year numeric columns
        for i, cell in enumerate(row[1:], 1):
            if i in year_cols:
                continue
            val = parse_dutch_number(str(cell))
            if val is not None and val != 0:
                header_label = headers[i] if i < len(headers) else f"col_{i}"
                results.append({
                    "label": label,
                    "year": None,
                    "value": val,
                    "raw": str(cell).strip(),
                    "column": str(header_label),
                })

    return results


def compute_financial_summary(chunks: list) -> str:
    """
    Given a list of RetrievedChunks, extract table data and compute
    programmatic summaries (deltas, percentages) for financial questions.

    Returns a markdown string with computed facts to prepend to context.
    """
    all_numbers = []

    for chunk in chunks:
        # Access table_json from chunk content or payload
        table_json = getattr(chunk, 'table_json', None)
        if not table_json:
            # Try to extract from content if it contains [FINANCIAL] marker
            content = getattr(chunk, 'content', '')
            if '[FINANCIAL]' not in content:
                continue
            # No table_json available, skip
            continue

        numbers = extract_table_numbers(table_json)
        title = getattr(chunk, 'title', 'Onbekend')
        for n in numbers:
            n['source'] = title
        all_numbers.extend(numbers)

    if not all_numbers:
        return ""

    # Group by label and year for comparison
    by_label: Dict[str, Dict[str, float]] = {}
    for n in all_numbers:
        label = n['label']
        year = n.get('year')
        if year:
            by_label.setdefault(label, {})[year] = n['value']

    # Compute year-over-year deltas for labels with multiple years
    lines = ["## Berekende financiële vergelijkingen\n"]
    lines.append("*Onderstaande cijfers zijn programmatisch berekend uit de brontabellen, niet door een taalmodel.*\n")

    comparisons_found = 0
    for label, years in sorted(by_label.items()):
        if len(years) < 2:
            continue
        sorted_years = sorted(years.items())
        parts = []
        for i in range(1, len(sorted_years)):
            y_prev, v_prev = sorted_years[i - 1]
            y_curr, v_curr = sorted_years[i]
            delta = v_curr - v_prev
            if v_prev != 0:
                pct = (delta / abs(v_prev)) * 100
                direction = "+" if delta >= 0 else ""
                parts.append(
                    f"  - {y_prev} → {y_curr}: {_fmt_number(v_prev)} → {_fmt_number(v_curr)} "
                    f"({direction}{_fmt_number(delta)}, {direction}{pct:.1f}%)"
                )
        if parts:
            lines.append(f"**{label}:**")
            lines.extend(parts)
            comparisons_found += 1

    if comparisons_found == 0:
        return ""

    return "\n".join(lines) + "\n"


def _fmt_number(n: float) -> str:
    """Format a number for Dutch readability."""
    if abs(n) >= 1_000_000_000:
        return f"€{n / 1_000_000_000:.1f} mrd"
    if abs(n) >= 1_000_000:
        return f"€{n / 1_000_000:.1f} mln"
    if abs(n) >= 1_000:
        return f"€{n:,.0f}".replace(",", ".")
    return f"€{n:.0f}"
