"""Deterministic list labels for numbering that a source document declares.

Only stored format identifiers whose displayed characters follow directly from
the stored counter are rendered. Unknown identifiers, unstarted references and
out-of-range values stay unresolved, so a label is never invented for a format
or a sequence the source does not determine.
"""

from __future__ import annotations

MAX_SEQUENCE_VALUE = 1_000
MAX_CIRCLED_VALUE = 20
MAX_LETTER_VALUE = 26

_ROMAN_UNITS = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)
_LETTER_ORIGINS = {
    "upper_letter": 0x41,
    "lower_letter": 0x61,
    "circled_upper_letter": 0x24B6,
    "circled_lower_letter": 0x24D0,
}

# w:numFmt values whose displayed characters are fixed. Other values, including
# legal, ordinal, text and locale-specific formats, stay unsupported.
OOXML_NUMBER_FORMATS = {
    "decimal": "decimal",
    "upperLetter": "upper_letter",
    "lowerLetter": "lower_letter",
    "upperRoman": "upper_roman",
    "lowerRoman": "lower_roman",
    "decimalEnclosedCircle": "circled_decimal",
}

# a:buAutoNum/@type values with their fixed surrounding characters. Font-mapped
# schemes such as the Wingdings circles are not reconstructed.
PRESENTATION_SCHEMES = {
    "arabicPlain": ("decimal", "{}"),
    "arabicPeriod": ("decimal", "{}."),
    "arabicParenR": ("decimal", "{})"),
    "arabicParenBoth": ("decimal", "({})"),
    "alphaUcPeriod": ("upper_letter", "{}."),
    "alphaLcPeriod": ("lower_letter", "{}."),
    "alphaUcParenR": ("upper_letter", "{})"),
    "alphaLcParenR": ("lower_letter", "{})"),
    "alphaUcParenBoth": ("upper_letter", "({})"),
    "alphaLcParenBoth": ("lower_letter", "({})"),
    "romanUcPeriod": ("upper_roman", "{}."),
    "romanLcPeriod": ("lower_roman", "{}."),
    "romanUcParenR": ("upper_roman", "{})"),
    "romanLcParenR": ("lower_roman", "{})"),
    "romanUcParenBoth": ("upper_roman", "({})"),
    "romanLcParenBoth": ("lower_roman", "({})"),
    "circleNumDbPlain": ("circled_decimal", "{}"),
}

# hp:paraHead/@numFormat values. Hangul, ideograph and symbol formats are not
# rendered because their displayed sequence is not fixed by the identifier.
HWP_NUMBER_FORMATS = {
    "DIGIT": "decimal",
    "CIRCLED_DIGIT": "circled_decimal",
    "ROMAN_CAPITAL": "upper_roman",
    "ROMAN_SMALL": "lower_roman",
    "LATIN_CAPITAL": "upper_letter",
    "LATIN_SMALL": "lower_letter",
    "CIRCLED_LATIN_CAPITAL": "circled_upper_letter",
    "CIRCLED_LATIN_SMALL": "circled_lower_letter",
}

OOXML_MARKER = "%"
HWP_MARKER = "^"


def render(value: int, style: str | None) -> str | None:
    """Return the displayed characters for one counter value, or None."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value == 0 and style == "decimal":
        return "0"
    if not 1 <= value <= MAX_SEQUENCE_VALUE:
        return None
    if style == "decimal":
        return str(value)
    if style == "circled_decimal":
        return chr(0x2460 + value - 1) if value <= MAX_CIRCLED_VALUE else None
    if style in _LETTER_ORIGINS:
        # Repeated-letter and multi-letter continuations differ between writers.
        if value > MAX_LETTER_VALUE:
            return None
        return chr(_LETTER_ORIGINS[style] + value - 1)
    if style in {"upper_roman", "lower_roman"}:
        remaining = value
        glyphs = []
        for unit, text in _ROMAN_UNITS:
            while remaining >= unit:
                glyphs.append(text)
                remaining -= unit
        result = "".join(glyphs)
        return result if style == "upper_roman" else result.lower()
    return None


def substitute(pattern: str | None, marker: str, resolve) -> str | None:
    """Replace `<marker><digit>` level references; other marker use is unresolved."""

    if not pattern:
        return None
    result: list[str] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character != marker:
            result.append(character)
            index += 1
            continue
        reference = pattern[index + 1] if index + 1 < len(pattern) else ""
        if len(reference) != 1 or reference not in "123456789":
            return None
        text = resolve(int(reference))
        if text is None:
            return None
        result.append(text)
        index += 2
    return "".join(result)


def scheme_label(scheme: str | None, value: int) -> str | None:
    """Return the displayed label of one presentation auto-number scheme."""

    entry = PRESENTATION_SCHEMES.get(scheme)
    if entry is None:
        return None
    style, template = entry
    text = render(value, style)
    return None if text is None else template.replace("{}", text)


class ListCounters:
    """Counters of one list identity inside one independent text flow.

    Levels are one-based. A level definition holds its `style`, `start`,
    `pattern` and whether a shallower level `restarts` it.
    """

    def __init__(self, levels: dict[int, dict], marker: str = OOXML_MARKER) -> None:
        self.levels = levels
        self.marker = marker
        self.values: dict[int, int] = {}

    def advance(self, level: int, *, start: int | None = None) -> int | None:
        """Move one paragraph forward at `level` and restart deeper levels."""

        definition = self.levels.get(level)
        current = self.values.get(level)
        if start is not None:
            value = start
        elif current is not None:
            value = current + 1
        elif definition is not None:
            value = definition.get("start")
        else:
            value = None
        if value is None:
            self.values.pop(level, None)
        else:
            self.values[level] = value
        for deeper in [key for key in self.values if key > level]:
            if self.levels.get(deeper, {}).get("restarts", True):
                del self.values[deeper]
        return value

    def label(self, level: int) -> str | None:
        """Return the displayed marker of the current paragraph, or None."""

        definition = self.levels.get(level)
        if definition is None or self.values.get(level) is None:
            return None

        def resolve(reference: int) -> str | None:
            if not 1 <= reference <= level:
                return None
            referenced = self.levels.get(reference)
            value = self.values.get(reference)
            if referenced is None or value is None:
                return None
            return render(value, referenced.get("style"))

        return substitute(definition.get("pattern"), self.marker, resolve)
