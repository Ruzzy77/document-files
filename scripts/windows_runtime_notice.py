"""Reject obviously mismatched compiler notices; never grant redistribution rights.

The current Windows CI selects Visual Studio 2022 (17.x). An extension's older
preview EULA is not that product's license even when it mentions redistribution.
This identity screen is additional to the independent review of the exact terms,
installed edition and runtime files. It is not a legal-compliance certificate.
"""

from __future__ import annotations

import re


def validate_notice(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("windows_runtime_notice_missing")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("windows_runtime_notice_too_large")
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = raw.decode(encoding, errors="strict")
    plain = " ".join(re.sub(r"\\[a-zA-Z]+-?\d* ?|[{}]", " ", text).split())
    heading = plain[:700]
    if (
        not re.search(r"MICROSOFT.{0,120}VISUAL\s+STUDIO.{0,100}2022", heading, re.I)
        or re.search(r"PRE[- ]?RELEASE\s+SOFTWARE|VS2015_CTP_EVAL", heading, re.I)
        or re.search(r"MICROSOFT\s+VISUAL\s+C\+\+.*RUNTIME", heading, re.I)
        or not re.search(r"distributable.{0,30}code|redistribut", plain, re.I)
    ):
        raise ValueError("windows_runtime_notice_product_mismatch")
    return {"product": "Visual Studio 2022", "independentRedistributionReview": "pending"}
