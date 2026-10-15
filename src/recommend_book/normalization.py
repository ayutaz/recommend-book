"""Comparison keys only: callers retain the untouched source values."""
import re
import unicodedata


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def normalize_isbn(value: str) -> str | None:
    """Validate ISBN-10/13 and return ISBN-13, or None for malformed input."""
    value = unicodedata.normalize("NFKC", value).upper()
    digits = re.sub(r"[\s\-‐‑‒–—]", "", value)
    if re.fullmatch(r"\d{9}[\dX]", digits):
        checksum = sum((10 - index) * (10 if char == "X" else int(char)) for index, char in enumerate(digits))
        if checksum % 11:
            return None
        body = "978" + digits[:9]
        return body + str((-sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(body))) % 10)
    if re.fullmatch(r"97[89]\d{10}", digits):
        checksum = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(digits))
        return digits if checksum % 10 == 0 else None
    return None
