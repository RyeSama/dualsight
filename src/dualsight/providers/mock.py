"""Deterministic mock provider used by the demo and the test suite.

It implements both halves of the bridge without any network access: the vision
half returns structured JSON, and the text half answers from the evidence block
it receives.  The content is deliberately synthetic and clearly labelled.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from ..chat import ChatRequest, ChatResult, StreamChunk, Usage

_MOCK_CARD = {
    "summary": (
        "A synthetic demo card on a deep blue gradient. The card is white, "
        "centered, and contains the word DualSight above a smaller subtitle."
    ),
    "ocr": [
        {"text": "DualSight", "lang": "en", "region": "center", "confidence": 0.99},
        {
            "text": "A perception bridge for text-only models",
            "lang": "en",
            "region": "center",
            "confidence": 0.96,
        },
    ],
    "objects": [
        {"name": "card", "count": 1, "details": "white, rounded corners", "region": "center"},
        {
            "name": "gradient background",
            "count": 1,
            "details": "blue to navy",
            "region": "full frame",
        },
    ],
    "layout": "A single centered card with two lines of text; generous empty margin around it.",
    "colors": ["blue", "white", "navy"],
    "chart": None,
    "uncertainties": ["This is deterministic mock output, not a real visual observation."],
    "suggested_questions": ["What does the card say?", "What colors are used?"],
}

_MOCK_RECEIPT = {
    "summary": (
        "A synthetic paper receipt from DualSight Mock Market. It lists three "
        "items, a subtotal, tax, and a total of $23.47."
    ),
    "ocr": [
        {"text": "DUALSIGHT MOCK MARKET", "lang": "en", "region": "top-center", "confidence": 0.99},
        {"text": "2026-10-19  14:32", "lang": "en", "region": "top-left", "confidence": 0.98},
        {
            "text": "1x Perception Pass      $12.00",
            "lang": "en",
            "region": "middle",
            "confidence": 0.97,
        },
        {
            "text": "1x Evidence Render       $6.50",
            "lang": "en",
            "region": "middle",
            "confidence": 0.97,
        },
        {
            "text": "1x Cache Hit             $0.00",
            "lang": "en",
            "region": "middle",
            "confidence": 0.97,
        },
        {
            "text": "SUBTOTAL                $18.50",
            "lang": "en",
            "region": "lower",
            "confidence": 0.98,
        },
        {
            "text": "TAX (9.5%)               $1.76",
            "lang": "en",
            "region": "lower",
            "confidence": 0.98,
        },
        {
            "text": "TOTAL                   $23.47",
            "lang": "en",
            "region": "bottom",
            "confidence": 0.99,
        },
        {"text": "PAID WITH MOCK CARD", "lang": "en", "region": "bottom", "confidence": 0.96},
    ],
    "objects": [
        {
            "name": "receipt",
            "count": 1,
            "details": "white thermal paper, slightly curled",
            "region": "center",
        },
        {"name": "text lines", "count": 9, "details": "monospace, black", "region": "full receipt"},
    ],
    "layout": "Header at the top, item rows in the middle, totals aligned at the bottom right.",
    "colors": ["white", "black", "gray"],
    "chart": None,
    "uncertainties": ["All values are synthetic mock data."],
    "suggested_questions": ["What is the total?", "How much was tax?", "Which item cost the most?"],
}

_MOCK_CHART = {
    "summary": (
        "A synthetic bar chart titled 'Weekly perception latency'. Four bars "
        "show median latency falling from 820 ms to 310 ms across four weeks."
    ),
    "ocr": [
        {
            "text": "Weekly perception latency",
            "lang": "en",
            "region": "top-center",
            "confidence": 0.98,
        },
        {"text": "Week 1  820 ms", "lang": "en", "region": "left", "confidence": 0.95},
        {"text": "Week 2  640 ms", "lang": "en", "region": "middle-left", "confidence": 0.95},
        {"text": "Week 3  470 ms", "lang": "en", "region": "middle-right", "confidence": 0.95},
        {"text": "Week 4  310 ms", "lang": "en", "region": "right", "confidence": 0.95},
    ],
    "objects": [
        {
            "name": "bar chart",
            "count": 1,
            "details": "four vertical bars, descending",
            "region": "center",
        },
        {
            "name": "axis labels",
            "count": 2,
            "details": "week and milliseconds",
            "region": "bottom and left",
        },
    ],
    "layout": "Title centered at the top; four bars left to right; value labels above each bar.",
    "colors": ["teal", "gray", "white"],
    "chart": {
        "type": "bar",
        "axes": "x=week; y=median latency in milliseconds",
        "series": ["median latency"],
        "values": ["820", "640", "470", "310"],
        "takeaway": "Latency drops by roughly 62% over four weeks.",
    },
    "uncertainties": ["Values are synthetic and approximate."],
    "suggested_questions": ["What is the trend?", "Which week was fastest?"],
}

_MOCK_UI = {
    "summary": (
        "A synthetic settings screen for a desktop application. A red banner "
        "reports that the API key is missing, and the Save button is disabled."
    ),
    "ocr": [
        {"text": "Settings", "lang": "en", "region": "top-left", "confidence": 0.99},
        {"text": "Vision provider", "lang": "en", "region": "left", "confidence": 0.97},
        {
            "text": "API key is missing. Add DUALSIGHT_VISION_API_KEY to continue.",
            "lang": "en",
            "region": "top banner",
            "confidence": 0.98,
        },
        {"text": "Save", "lang": "en", "region": "bottom-right", "confidence": 0.98},
    ],
    "objects": [
        {"name": "error banner", "count": 1, "details": "red, dismissible", "region": "top"},
        {"name": "Save button", "count": 1, "details": "disabled, gray", "region": "bottom-right"},
        {"name": "form fields", "count": 3, "details": "provider, key, model", "region": "center"},
    ],
    "layout": "A left navigation column, a main form, and a footer action bar.",
    "colors": ["white", "gray", "red"],
    "chart": None,
    "uncertainties": ["This is a synthetic interface mock."],
    "suggested_questions": ["What is the error?", "Why can't I save?"],
}

_MOCK_DIAGRAM = {
    "summary": (
        "A synthetic architecture diagram with three boxes: Image, Vision model, "
        "and Reasoning model. Arrows flow left to right through an evidence block."
    ),
    "ocr": [
        {"text": "Image", "lang": "en", "region": "left", "confidence": 0.98},
        {"text": "Vision model", "lang": "en", "region": "center-left", "confidence": 0.98},
        {"text": "Evidence", "lang": "en", "region": "center", "confidence": 0.98},
        {"text": "Reasoning model", "lang": "en", "region": "right", "confidence": 0.98},
    ],
    "objects": [
        {"name": "box", "count": 4, "details": "rounded rectangles", "region": "across the frame"},
        {"name": "arrow", "count": 3, "details": "left to right", "region": "between boxes"},
    ],
    "layout": "Four nodes in a horizontal pipeline, connected by three arrows.",
    "colors": ["white", "blue", "gray"],
    "chart": None,
    "uncertainties": ["This is a synthetic diagram mock."],
    "suggested_questions": ["What connects to the reasoning model?"],
}

_MOCK_PHOTO = {
    "summary": (
        "A synthetic desk scene: a laptop showing a code editor, a white coffee "
        "cup, and a small plant, lit by soft window light."
    ),
    "ocr": [
        {"text": "dualsight/", "lang": "en", "region": "laptop screen", "confidence": 0.9},
    ],
    "objects": [
        {"name": "laptop", "count": 1, "details": "open, silver", "region": "center"},
        {"name": "coffee cup", "count": 1, "details": "white ceramic", "region": "right"},
        {"name": "plant", "count": 1, "details": "small, green leaves", "region": "left"},
    ],
    "layout": "Laptop in the center foreground, cup to the right, plant to the left.",
    "colors": ["silver", "white", "green"],
    "chart": None,
    "uncertainties": ["This is a synthetic scene mock; no real people are depicted."],
    "suggested_questions": ["What is on the desk?", "What is on the screen?"],
}

_BY_PROFILE = {
    "receipt": _MOCK_RECEIPT,
    "document": _MOCK_RECEIPT,
    "chart": _MOCK_CHART,
    "ui": _MOCK_UI,
    "diagram": _MOCK_DIAGRAM,
    "photo": _MOCK_PHOTO,
}


def _mock_usage(prompt_text: str, completion_text: str) -> Usage:
    prompt_tokens = max(1, len(prompt_text) // 4)
    completion_tokens = max(1, len(completion_text) // 4)
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


def _all_text(request: ChatRequest) -> str:
    return "\n".join(message.text() for message in request.messages)


def _vision_payload(prompt: str) -> dict[str, Any]:
    match = re.search(r"Perception profile:\s*(\w+)", prompt)
    profile = match.group(1).lower() if match else "auto"
    payload = _BY_PROFILE.get(profile, _MOCK_CARD)
    return json.loads(json.dumps(payload))


def _question_from(prompt: str) -> str:
    match = re.search(r"Question:\s*(.+)", prompt, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else prompt.strip()


def _answer_question(question: str, payload: dict[str, Any]) -> str:
    lower = question.lower()
    ocr = "\n".join(item["text"] for item in payload.get("ocr", []))
    if any(word in lower for word in ("total", "how much", "cost", "price")):
        for line in ocr.splitlines():
            if re.match(r"^TOTAL\b", line.strip(), flags=re.IGNORECASE):
                return f"The receipt shows: {line.strip()}."
    if any(word in lower for word in ("text", "say", "read", "written")) and ocr:
        return "The visible text reads:\n" + ocr
    if "trend" in lower or "chart" in lower:
        chart = payload.get("chart")
        if chart:
            return f"{chart.get('takeaway', '')} Values: {', '.join(chart.get('values', []))}."
    summary = payload.get("summary", "")
    if ocr:
        return f"{summary}\n\nVisible text includes: {ocr.splitlines()[0]}"
    return summary


def _mock_vision_answer(prompt: str) -> str:
    # For /v1/vision/ask, reuse the profile payload but answer the question.
    payload = _vision_payload(prompt)
    question = _question_from(prompt)
    return _answer_question(question, payload)


def _extract_evidence(text: str) -> str:
    matches = re.findall(
        r"<<<VISUAL_EVIDENCE[^>]*>>>(.*?)<<<END_VISUAL_EVIDENCE[^>]*>>>",
        text,
        re.DOTALL,
    )
    return "\n\n".join(match.strip() for match in matches)


def _summary_from_evidence(evidence: str) -> str:
    match = re.search(r"^summary:\s*(.+)$", evidence, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _ocr_from_evidence(evidence: str) -> list[str]:
    lines = evidence.splitlines()
    try:
        start = lines.index("visible_text_verbatim:")
    except ValueError:
        return []
    result: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith(("objects:", "layout:", "colors:", "chart:", "uncertainties:")):
            break
        if line.strip():
            result.append(line.strip())
    return result


def _mock_bridge_answer(request: ChatRequest) -> str:
    text = _all_text(request)
    evidence = _extract_evidence(text)
    if not evidence:
        return (
            "I don't see any image evidence in this request. Attach an image or an "
            "image URL, and I'll ask the vision model to inspect it first."
        )
    if 'status="unavailable"' in evidence:
        return (
            "The vision model could not analyze the attached image, so I can't "
            "describe it reliably. Please check the image or the vision API settings."
        )
    summary = _summary_from_evidence(evidence)
    ocr = _ocr_from_evidence(evidence)
    question = ""
    # The original user question is the last non-bridge text in the request.
    for message in reversed(request.messages):
        if message.role != "user":
            continue
        body = message.text()
        if "DUALSIGHT BRIDGE" in body:
            body = re.sub(
                r"\[DUALSIGHT BRIDGE.*?\[/DUALSIGHT BRIDGE[^\]]*\]", "", body, flags=re.DOTALL
            )
        body = re.sub(
            r"<<<VISUAL_EVIDENCE.*?<<<END_VISUAL_EVIDENCE[^>]*>>>", "", body, flags=re.DOTALL
        )
        if body.strip():
            question = body.strip()
            break

    if any(word in question.lower() for word in ("total", "how much", "cost")):
        for line in ocr:
            label = re.sub(r"\s+", " ", line.split("]")[-1]).strip()
            if re.match(r"^TOTAL\b", label, flags=re.IGNORECASE):
                return f"According to the receipt in the image, {label.lower()}."
    if any(word in question.lower() for word in ("text", "say", "read")) and ocr:
        return "The image contains this visible text:\n" + "\n".join(ocr)
    answer = summary or "The image contains visual information, but the evidence is sparse."
    if ocr:
        answer += "\n\nVisible text includes: " + " | ".join(ocr[:3])
    return (
        answer + "\n\n(Mock mode: this answer is generated locally from the mock evidence block.)"
    )


class MockProvider:
    name = "mock"
    supports_tools = False

    def __init__(self, *, model: str = "mock-vision", **_ignored: Any) -> None:
        self.model = model

    async def complete(self, request: ChatRequest) -> ChatResult:
        await asyncio.sleep(0)
        text = _all_text(request)
        system_text = "\n".join(
            message.text() for message in request.messages if message.role == "system"
        )
        if "Answer the question below" in text:
            answer = _mock_vision_answer(text)
            return ChatResult(
                text=answer,
                finish_reason="stop",
                usage=_mock_usage(text, answer),
                raw={"mock": True},
            )
        if "perception module" in system_text or "Perception profile:" in text:
            payload = _vision_payload(text)
            answer = json.dumps(payload, ensure_ascii=False, indent=2)
            return ChatResult(
                text=answer,
                finish_reason="stop",
                usage=_mock_usage(text, answer),
                raw={"mock": True},
            )
        answer = _mock_bridge_answer(request)
        return ChatResult(
            text=answer,
            finish_reason="stop",
            usage=_mock_usage(text, answer),
            raw={"mock": True},
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        result = await self.complete(request)
        for word in re.findall(r"\S+\s*", result.text):
            yield StreamChunk(delta_text=word)
        yield StreamChunk(finish_reason=result.finish_reason, usage=result.usage)

    async def aclose(self) -> None:
        return None
