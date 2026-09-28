"""Door codes and passwords: one pattern for the renderer (which rejects them in listing copy)
and the digest (which redacts them before Claude reads the live listing)."""
from __future__ import annotations

import re

# Codes and passwords belong in the check-in message, never the public listing.
# "The Wi-Fi password is in the welcome book" is fine; "password: Pine123" is not.
SECRET_RE = re.compile(r"""(?ix)
    \b(?:door|entry|lock\s?box|keypad|gate|garage|building|lock)\s+code\s*(?:is|:|=)?\s*\#?\d{3,}
  | \bcode\s*(?::|=|\bis\b)\s*\#?\d{3,}
  | \bpass(?:word|code)?\s*(?::|=|\bis\b)\s*
    (?!(?:in|on|at|with|inside|posted|printed|sent|shared|provided|available|the|your|our|a)\b)\S+
""")


def redact(text: str) -> tuple[str, int]:
    """Replace every code or password with [REDACTED]. Returns (text, how many)."""
    return SECRET_RE.subn("[REDACTED]", text)
