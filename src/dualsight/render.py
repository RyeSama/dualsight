"""Render a perception result into compact, delimited text evidence."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Perception


@dataclass(frozen=True, slots=True)
class RenderedEvidence:
    text: str
    truncated: bool = False


def _clean(value: str) -> str:
    # The reasoning model should never see a delimiter it could mistake for the
    # real end of an evidence block. The random nonce already makes forgery
    # impractical; this is defence in depth.
    return value.replace("<<<", "\u2039\u2039\u2039").replace(">>>", "\u203a\u203a\u203a")


def _one_line(value: str) -> str:
    return " ".join(value.split())


def render_evidence(
    perception: Perception,
    *,
    label: str,
    nonce: str,
    max_chars: int,
    source: str | None = None,
) -> RenderedEvidence:
    """Render one image's perception as a delimited evidence block."""

    lines: list[str] = [f'<<<VISUAL_EVIDENCE {nonce} image="{_clean(label)}">>>']
    if source:
        lines.append(f"source: {_clean(source)}")
    lines.append(f"summary: {_clean(perception.summary.strip())}")

    ocr_text = perception.ocr_text.strip()
    if ocr_text:
        lines.append("visible_text_verbatim:")
        for span in perception.ocr:
            text = span.text.strip()
            if not text:
                continue
            meta: list[str] = []
            if span.region:
                meta.append(span.region)
            if span.lang:
                meta.append(span.lang)
            if span.confidence is not None:
                meta.append(f"{span.confidence:.2f}")
            prefix = f"[{', '.join(meta)}] " if meta else ""
            for text_line in text.splitlines():
                lines.append(f"{prefix}{_clean(text_line)}")

    if perception.objects:
        lines.append("objects:")
        for obj in perception.objects:
            bits = [obj.name]
            if obj.count is not None and obj.count > 1:
                bits.append(f"x{obj.count}")
            if obj.region:
                bits.append(f"({obj.region})")
            if obj.details:
                bits.append(f"- {_clean(obj.details)}")
            lines.append("- " + " ".join(bits))

    if perception.layout and perception.layout.strip():
        lines.append(f"layout: {_clean(_one_line(perception.layout))}")
    if perception.colors:
        lines.append("colors: " + ", ".join(_clean(color) for color in perception.colors))
    if perception.chart is not None:
        chart = perception.chart
        chart_parts: list[str] = []
        if chart.type:
            chart_parts.append(f"type={_clean(chart.type)}")
        if chart.axes:
            chart_parts.append(f"axes={_clean(chart.axes)}")
        if chart.series:
            chart_parts.append("series=" + ", ".join(_clean(item) for item in chart.series))
        if chart.values:
            chart_parts.append("values=" + ", ".join(_clean(item) for item in chart.values))
        if chart.takeaway:
            chart_parts.append(f"takeaway={_clean(chart.takeaway)}")
        lines.append("chart: " + " | ".join(chart_parts))
    if perception.uncertainties:
        lines.append("uncertainties:")
        for item in perception.uncertainties:
            text = item.strip()
            if text:
                lines.append(f"- {_clean(text)}")
    lines.append(f"<<<END_VISUAL_EVIDENCE {nonce}>>>")

    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return RenderedEvidence(rendered)

    footer = f"\n<<<END_VISUAL_EVIDENCE {nonce}>>>"
    marker = "\n[... visual evidence truncated by DualSight ...]"
    # Keep the closing delimiter even when truncating: downstream prompts rely on
    # the block being well formed, and an unclosed block looks like a hijack.
    budget = max_chars - len(marker) - len(footer)
    if budget < 200:
        return RenderedEvidence(
            f'<<<VISUAL_EVIDENCE {nonce} image="{_clean(label)}">>>{marker}{footer}',
            truncated=True,
        )
    cut = rendered[:budget]
    # Prefer the last complete line so we do not cut a token in half.
    if "\n" in cut:
        cut = cut.rsplit("\n", 1)[0]
    return RenderedEvidence(cut.rstrip() + marker + footer, truncated=True)


def render_unavailable_evidence(
    *,
    label: str,
    error: str,
    nonce: str,
    max_chars: int = 600,
) -> RenderedEvidence:
    """Render an honest failure block instead of silently dropping an image."""

    header = f'<<<VISUAL_EVIDENCE {nonce} image="{_clean(label)}" status="unavailable">>>\n'
    body = (
        f"The vision model could not analyze this image: {_clean(error)}\n"
        "Do not claim to see or describe this image. Tell the user that visual "
        "analysis was unavailable if it matters to their request."
    )
    footer = f"\n<<<END_VISUAL_EVIDENCE {nonce}>>>"
    marker = "\n[... truncated ...]"
    room = max_chars - len(header) - len(footer)
    if room <= 0:
        return RenderedEvidence(header.rstrip() + footer, truncated=True)
    if len(body) > room:
        body = body[: max(0, room - len(marker))].rstrip() + marker
        return RenderedEvidence(header + body + footer, truncated=True)
    return RenderedEvidence(header + body + footer)
