"""The licence gate.

Every imported piece carries a sidecar recording where it came from and under what
terms.  An unresolved licence is a **hard stop**, not a warning: nothing without a
sidecar whose licence identifier is on the allowlist can be resolved by the catalogue,
so an unlicensed pack physically cannot end up in a build.

The allowlist below is the server owner's call, recorded here rather than inferred: the server is
subsidised and takes no money, so non-commercial clauses are acceptable.  Change the
policy by editing this file, not by special-casing a pack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# SPDX-ish identifiers, matched case-insensitively as whole strings.
ALLOWED_PATTERNS: tuple[str, ...] = (
    r"MIT",
    r"Apache-2\.0",
    r"BSD-.*",
    r"CC0-.*", r"CC0",
    r"CC-BY-\d.*", r"CC-BY",
    r"CC-BY-SA-.*",
    r"CC-BY-NC-.*",          # includes CC-BY-NC-SA-* and CC-BY-NC-ND-*
    r"MPL-.*", r"MPL",
    r"LGPL-.*",
    r"GPL-.*", r"AGPL-.*",
    r"Unlicense",
    r"Zlib",
)

# Explicit hard stops, checked first so the reason is specific.
BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"LicenseRef-All-Rights-Reserved", "all rights reserved: no redistribution grant"),
    (r"ARR", "all rights reserved: no redistribution grant"),
    (r"LicenseRef-.*", "custom/non-SPDX licence: must be read by a human before use"),
    (r"", "no licence declared"),
)

# Clauses that do not block under this policy but must be surfaced on the sidecar.
CAVEATS: tuple[tuple[str, str], ...] = (
    (r"CC-BY-NC-ND-.*", "ND: no derivative works — pieces may be placed but never edited"),
    (r"CC-BY-NC-.*", "NC: non-commercial only — fine while the server takes no money"),
    (r"CC-BY-SA-.*", "SA: share-alike — derived structure files inherit this licence"),
    (r"[AL]?GPL-.*", "copyleft: redistribute the pack unmodified, alongside its own licence"),
)


@dataclass(frozen=True)
class Verdict:
    licence: str
    allowed: bool
    reason: str
    caveat: str = ""

    @property
    def commercial_ok(self) -> bool:
        return not re.match(r"(?i)^CC-BY-NC", self.licence or "")


def _full(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value, flags=re.IGNORECASE) is not None


def verdict(licence_id: str | None) -> Verdict:
    """Decide whether ``licence_id`` may be ingested.  Unknown => refused."""
    lic = (licence_id or "").strip()
    for pat, reason in BLOCKED_PATTERNS:
        if _full(pat, lic):
            return Verdict(lic, False, reason)
    for pat in ALLOWED_PATTERNS:
        if _full(pat, lic):
            caveat = next((c for p, c in CAVEATS if _full(p, lic)), "")
            return Verdict(lic, True, "on the allowlist", caveat)
    return Verdict(lic, False, f"licence {lic!r} is not on the allowlist")
