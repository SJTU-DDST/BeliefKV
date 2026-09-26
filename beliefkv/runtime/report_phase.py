"""Bounded, content-free tracking of headings in a streamed child report."""

from __future__ import annotations

import re

_HEADING = re.compile(r"^ {0,3}#{2,4} +(.+?) *#* *$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CLOSING = {
    "summary": ("summary", "final summary", "executive summary"),
    "conclusion": ("conclusion", "conclusions", "concluding remarks"),
    "next_steps": ("next steps", "recommendations", "follow-up"),
    "validation": ("verification", "validation", "testing", "tests"),
}


class ReportPhaseTracker:
    """Accept streamed content exactly once per chunk; never retain full text."""

    def __init__(self) -> None:
        self.chars = 0
        self._line = ""
        self._line_truncated = False
        self._fence: str | None = None
        self._emitted = 0

    def feed(self, text: str) -> list[tuple[str, int]]:
        phases = []
        for char in text:
            self.chars += 1
            if char != "\n":
                if len(self._line) < 160:
                    self._line += char
                else:
                    self._line_truncated = True
                continue
            line = self._line.rstrip("\r")
            truncated = self._line_truncated
            self._line = ""
            self._line_truncated = False
            if truncated:
                continue
            fence = _FENCE.match(line)
            if fence:
                marker = fence.group(1)
                if self._fence is None:
                    self._fence = marker[0]
                elif self._fence == marker[0]:
                    self._fence = None
                continue
            if self._fence is not None or self._emitted >= 64:
                continue
            heading = _HEADING.match(line)
            if heading is None:
                continue
            title = re.sub(
                r"^\d{1,2}[.)]\s*", "", heading.group(1).strip().lower()
            ).strip("*_ ")
            phase = "other"
            for name, prefixes in _CLOSING.items():
                if any(
                    title == prefix
                    or title.startswith(prefix + " ")
                    or title.startswith(prefix + ":")
                    for prefix in prefixes
                ):
                    phase = name
                    break
            self._emitted += 1
            phases.append((phase, self.chars))
        return phases
