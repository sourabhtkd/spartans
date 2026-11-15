import re
from typing import Any

try:  # optional layer: if the library is missing or broken, the regex layers still mask
    from indic_pii import PIIDetector as _IndicDetector
except Exception:  # noqa: BLE001  # pragma: no cover
    _IndicDetector = None  # type: ignore[assignment,misc]

# Verhoeff algorithm lookup tables
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]

_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]

_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def is_luhn_valid(num_str: str) -> bool:
    digits = [int(c) for c in num_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            doubled = digit * 2
            checksum += (doubled - 9) if doubled > 9 else doubled
        else:
            checksum += digit
    return checksum % 10 == 0


def is_verhoeff_valid(num_str: str) -> bool:
    digits = [int(c) for c in num_str if c.isdigit()]
    if len(digits) != 12:
        return False
    c = 0
    for i, digit in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][digit]]
    return c == 0


def generate_verhoeff_check_digit(num_str: str) -> int:
    """Computes the Verhoeff check digit for an 11-digit string to form a 12-digit number."""
    digits = [int(c) for c in num_str if c.isdigit()]
    c = 0
    for i, digit in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][digit]]
    return _VERHOEFF_INV[c]


# Regex patterns
API_KEY_REGEX = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{16,}|gsk_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
)
EMAIL_REGEX = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
CARD_REGEX = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# 12 digits starting 2-9, with spaces/hyphens anywhere (users type "23456 7890 123" as often as
# "2345 6789 0123"). Masked regardless of checksum: over-masking beats leaking.
AADHAAR_REGEX = re.compile(r"(?<![\d-])[2-9](?:[ -]?\d){11}(?![ -]?\d)")
# Phone regex: Indian (+91 9876543210 or 9876543210) and international (+1-555-1234567)
PHONE_INDIA_REGEX = re.compile(r"(?:\+?91[ -]?)?[6-9]\d{9}\b")
PHONE_INTL_REGEX = re.compile(r"\+\d{1,3}(?:[ -]?\d){6,14}\b")


# indic-pii types we act on -> our tag. Passport / voter-ID / driving-licence / DOB are left out on
# purpose: their patterns also match ordinary code identifiers (e.g. `x1234567`) in prompts.
_INDIC_TAGS = {
    "AADHAAR": "[AADHAAR]",
    "PAN": "[PAN]",
    "UPI": "[UPI]",
    "IFSC": "[IFSC]",
    "PHONE": "[PHONE]",
    "BANK_ACCOUNT": "[BANK_ACCOUNT]",
}
# validate_aadhaar=False: mask any well-formed 12-digit Aadhaar, even when the checksum fails
# (typos, test data). Over-masking a number is safer than leaking one.
_indic = _IndicDetector(use_ner=False, validate_aadhaar=False) if _IndicDetector else None


def _indic_layer(text: str) -> str:
    """Replace spans found by indic-pii with our tags. Never raises: on error the text is unchanged."""
    if _indic is None:
        return text
    try:
        matches = [m for m in _indic.detect(text) if m.pii_type in _INDIC_TAGS]
        for m in sorted(matches, key=lambda m: m.start, reverse=True):
            raw = text[m.start : m.end]  # the phone pattern can swallow a leading space
            start = m.start + (len(raw) - len(raw.lstrip()))
            end = m.end - (len(raw) - len(raw.rstrip()))
            text = text[:start] + _INDIC_TAGS[m.pii_type] + text[end:]
    except Exception:  # noqa: BLE001, S110  # pragma: no cover - fail open to regex layers
        pass
    return text


def mask_text(text: str) -> str:
    """Masks PII in text following strict precedence order.
    Order:
      1. API keys -> [SECRET]
      2. Email -> [EMAIL]
      3. Card (Luhn check) -> [CARD]
      4. indic-pii layer: Aadhaar, PAN, UPI, IFSC, phone, bank account -> [AADHAAR] [PAN] ...
      5. Aadhaar (any 12-digit grouping) -> [AADHAAR]   (backstop)
      6. Phone -> [PHONE]                         (backstop)
    """
    if not text:
        return text

    # 1. API key
    text = API_KEY_REGEX.sub("[SECRET]", text)

    # 2. Email
    text = EMAIL_REGEX.sub("[EMAIL]", text)

    # 3. Card (validate with Luhn)
    def card_replacer(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if 13 <= len(digits) <= 19 and is_luhn_valid(digits):
            return "[CARD]"
        return raw

    text = CARD_REGEX.sub(card_replacer, text)

    # 4. indic-pii layer (Aadhaar without checksum, PAN, UPI, IFSC, phone, bank account)
    text = _indic_layer(text)

    # 5. Aadhaar: any 12-digit grouping (checksum not required, matching the indic layer's policy)
    text = AADHAAR_REGEX.sub("[AADHAAR]", text)

    # 6. Phone
    # First international with +
    text = PHONE_INTL_REGEX.sub("[PHONE]", text)
    # Then Indian
    text = PHONE_INDIA_REGEX.sub("[PHONE]", text)

    return text


def mask_obj(obj: Any) -> Any:
    """Recursively masks text within dicts, lists, tuples, and strings."""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_obj(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(mask_obj(item) for item in obj)
    return obj
