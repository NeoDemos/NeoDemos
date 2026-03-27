"""
json_repair.py — Multi-layer JSON repair for LLM outputs.

Handles the most common failure modes from Gemini:
  1. Invalid backslash escapes  (e.g. \H, \P instead of H, P)
  2. Raw control characters     (literal newlines/tabs inside strings)
  3. Missing commas             (}{ instead of },{)
  4. Trailing commas            ([1, 2, ] or {"a": 1, })
  5. Truncated / unterminated   (response cut off mid-string)
"""

import json
import re


def repair_and_load(s: str):
    """Parse a JSON string, applying progressive repair layers if needed."""

    # Layer 1: Clean invalid backslash escapes (common in LLM output)
    # Keeps valid escapes: \/ \" \b \f \n \r \t \u  and \\
    s = re.sub(r'\\([^\\/"bfnrtu])', r'\1', s)

    # Layer 2: Try strict=False to handle raw control characters
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        pass

    # Layer 3: Structural repairs
    s = s.strip()
    # Fix missing commas between objects:  }\n{  ->  },\n{   and  }{  ->  },{
    s = re.sub(r'\}\s*\n\s*\{', '},\n{', s)
    s = re.sub(r'\}\s*\{', '},{', s)
    # Fix trailing commas
    s = re.sub(r',\s*\]', ']', s)
    s = re.sub(r',\s*\}', '}', s)

    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        pass

    # Layer 4: Try raw_decode to extract valid JSON prefix
    try:
        decoder = json.JSONDecoder(strict=False)
        obj, idx = decoder.raw_decode(s)
        return obj if isinstance(obj, list) else [obj]
    except json.JSONDecodeError:
        pass

    # Layer 5: Character-level bracket counter for truncated responses
    # Finds the last complete top-level object and closes the array there.
    depth = 0
    in_string = False
    escape_next = False
    last_complete_obj_end = -1

    for i, ch in enumerate(s):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
            if depth == 1 and ch == '}':
                # Just closed a top-level object inside the outer array
                last_complete_obj_end = i + 1

    if last_complete_obj_end > 0:
        truncated = s[:last_complete_obj_end] + ']'
        truncated = re.sub(r'\}\s*\{', '},{', truncated)
        truncated = re.sub(r',\s*\]', ']', truncated)
        try:
            result = json.loads(truncated, strict=False)
            print(f"    \u2139 Recovered {len(result)} chunks from truncated JSON")
            return result
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("All repair layers exhausted", s, 0)
