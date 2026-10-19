# Perception schema

The vision model returns one JSON object. DualSight validates it into `Perception` and reduces it to bounded text evidence. The schema is intentionally small: enough structure to ground answers, not a full scene graph.

## JSON shape

```json
{
  "summary": "A paper receipt from Mock Market for three line items.",
  "ocr": [
    {"text": "TOTAL", "lang": "en", "region": "bottom right", "confidence": 0.99},
    {"text": "$23.47", "lang": "en", "region": "bottom right", "confidence": 0.98}
  ],
  "objects": [
    {"name": "text line", "count": 12, "details": "monospace, black", "region": "body"},
    {"name": "barcode", "count": 1, "details": "vertical black bars", "region": "bottom"}
  ],
  "layout": "Centered header, item table in the middle, total at the bottom right.",
  "colors": ["white", "black", "blue"],
  "chart": null,
  "uncertainties": ["The last digit of the order number is slightly blurred."],
  "suggested_questions": ["What is the total?", "What was purchased?"]
}
```

## Fields

### `summary` — string

What a careful human would say the image is, in one or two sentences. Literal, not interpretive. Required in practice; if the model omits it, the parser uses a plain-text fallback.

### `ocr` — array of spans

Visible text, transcribed verbatim. Preserve line breaks between separate spans, not inside a word. Do not translate, correct spelling, or infer missing characters.

| Key | Type | Meaning |
| --- | --- | --- |
| `text` | string | Exact visible text. |
| `lang` | string or null | BCP-47-ish language tag when known (`en`, `zh`, `ja`). |
| `region` | string or null | Human location: `top left`, `bottom right`, `header`, `table row 3`. |
| `confidence` | number 0–1 or null | Model confidence. Optional. |

`Perception.ocr_text` joins spans with newlines; evidence rendering preserves that order. A `TOTAL` line and a `SUBTOTAL` line are both kept; the reasoning model decides which matters.

### `objects` — array

Discrete visual elements, not a prose description.

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | `bar`, `button`, `logo`, `barcode`, `face`, `table`. |
| `count` | integer ≥0 or null | How many. |
| `details` | string or null | Color, material, state, text on the object. |
| `region` | string or null | Where it is. |

### `layout` — string or null

Spatial relationships that matter for reading: columns, alignment, reading order, overlapping elements, whether something is cut off.

### `colors` — array of strings

Dominant colors in plain words (`white`, `navy`, `teal`). Not hex codes; the goal is human-readable grounding.

### `chart` — object or null

Present only when the image contains a chart, graph, plot, or table with numeric structure.

```json
{
  "type": "bar",
  "axes": "x = week, y = latency in milliseconds",
  "series": ["median end-to-end time"],
  "values": ["Week 1: 820 ms", "Week 2: 640 ms", "Week 3: 470 ms", "Week 4: 310 ms"],
  "takeaway": "Latency falls in each of the four weeks."
}
```

Values are strings so units and labels survive exactly. Do not compute derived statistics unless the chart prints them.

### `uncertainties` — array of strings

Specific reasons an observation may be wrong: blur, glare, occlusion, tiny text, ambiguous icon, cropped edge. Empty array when the image is clear.

### `suggested_questions` — array of strings

Useful questions the image can answer. These are hints, not answers; the bridge does not auto-ask them.

## Leniency

Real models sometimes return:

- prose around the JSON;
- fenced code blocks;
- an envelope such as `{"perception": {...}}` or `{"data": {...}}`;
- a bare caption;
- strings where arrays are expected.

`parse_perception` tries strict JSON, fenced JSON, the outermost object, every embedded object, then falls back to `Perception(summary=raw_text, uncertainties=["The perception model did not return structured JSON."])`. String `ocr`, `objects`, `uncertainties`, and `colors` are coerced into one-item lists.

If the vision provider returns empty text, `Perceiver` raises `VisionError`; it does not fabricate a perception.

## Evidence rendering

Only a subset is rendered, in this order:

1. `summary`
2. `visible_text_verbatim` (OCR spans)
3. `objects`
4. `layout`
5. `colors`
6. `chart`
7. `uncertainties`

The block is capped by `DUALSIGHT_MAX_EVIDENCE_CHARS`, split across unique images. Truncation preserves the closing delimiter. `suggested_questions` is available in the API perception payload but is not injected into the text prompt, to keep the evidence compact.

## Extending the schema

1. Add the field to `models.Perception` with a safe default.
2. Ask for it in `prompts.PERCEPTION_SYSTEM_PROMPT`.
3. Render it in `render.render_evidence`.
4. Bump `PROMPT_VERSION` (the cache key includes it).
5. Add a parser and rendering test.

Keep fields optional and defaulted; a missing field from a real model must never fail a request.
