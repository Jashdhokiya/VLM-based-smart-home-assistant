import ast
import json
import re
from typing import Any, Dict, Iterable, Tuple

def _extract_dict_substring(text: str) -> str:
    """Finds the first balanced {...} substring in text."""
    start = text.find("{")
    if start == -1:
        raise ValueError("No '{' found in text")
    
    depth = 0
    in_quote = False
    quote_char = ""
    escape = False

    for i in range(start, len(text)):
        char = text[i]

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char in ('"', "'"):
            if not in_quote:
                in_quote = True
                quote_char = char
            elif quote_char == char:
                in_quote = False
            continue

        if not in_quote:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    raise ValueError("Unmatched '{' in text; no complete dictionary found")

def parse_dict(text: str) -> Dict[str, Any]:
    """
    Strips markdown code blocks, extracts the first {...} block,
    and parses it via json.loads or ast.literal_eval.
    Raises ValueError if parsing fails or result is not a dict.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Input text is empty or not a string")

    # Strip markdown code blocks like ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"```(?:json|python)?\s*([\s\S]*?)\s*```", r"\1", text).strip()

    dict_str = _extract_dict_substring(cleaned)

    # Attempt 1: Standard JSON
    try:
        parsed = json.loads(dict_str)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Attempt 2: ast.literal_eval for single quotes or Python-formatted dicts
    try:
        parsed = ast.literal_eval(dict_str)
        if isinstance(parsed, dict):
            return parsed
    except Exception as e:
        raise ValueError(f"Failed to safely parse dictionary from '{dict_str}': {e}") from e

    raise ValueError(f"Extracted content is not a dictionary: {type(parsed).__name__}")

def parse_device_commands(text: str, allowed: Iterable[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Parses device commands from text.
    Validates devices against allowed list (case-insensitively) and normalises to canonical form.
    Validates commands (case-insensitively) and normalises to 'On' or 'Off'.
    Returns (valid_commands, rejected_reasons).
    """
    allowed_list = list(allowed)
    allowed_map = {name.lower(): name for name in allowed_list}

    try:
        raw_dict = parse_dict(text)
    except Exception as e:
        return {}, {"_raw": f"Failed to parse dictionary: {e}"}

    valid: Dict[str, str] = {}
    rejected: Dict[str, str] = {}

    for raw_dev, raw_cmd in raw_dict.items():
        dev_str = str(raw_dev).strip()
        dev_lower = dev_str.lower()

        if dev_lower not in allowed_map:
            rejected[dev_str] = f"Device '{dev_str}' is not in allowed devices: {allowed_list}"
            continue

        canonical_dev = allowed_map[dev_lower]
        cmd_str = str(raw_cmd).strip().lower()

        if cmd_str == "on":
            canonical_cmd = "On"
        elif cmd_str == "off":
            canonical_cmd = "Off"
        else:
            rejected[dev_str] = f"Invalid command '{raw_cmd}'. Expected 'On' or 'Off'."
            continue

        valid[canonical_dev] = canonical_cmd

    return valid, rejected

def parse_device_counts(text: str) -> Dict[str, int]:
    """
    Parses natural language processor output into device count dictionary.
    Keys must be non-empty lowercase strings, values integers between 1 and 10.
    """
    raw_dict = parse_dict(text)
    result: Dict[str, int] = {}

    for k, v in raw_dict.items():
        key = str(k).strip().lower()
        if not key:
            raise ValueError("Device name cannot be empty")

        try:
            # Check if integer representation is exact
            count = int(v)
            if float(v) != count:
                raise ValueError(f"Non-integer value: {v}")
        except Exception as e:
            raise ValueError(f"Count for '{key}' must be an integer, got: {v}") from e

        if not (1 <= count <= 10):
            raise ValueError(f"Count for '{key}' must be between 1 and 10, got: {count}")

        result[key] = count

    return result
