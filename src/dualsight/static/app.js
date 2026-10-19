(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  const els = {
    health: $("#mode-badge"),
    modeText: $("#mode-text"),
    sampleNote: $("#sample-note"),
    dropzone: $("#dropzone"),
    fileInput: $("#file-input"),
    dropEmpty: $("#drop-empty"),
    preview: $("#preview"),
    previewImg: $("#preview-img"),
    previewName: $("#preview-name"),
    previewMeta: $("#preview-meta"),
    removeImage: $("#remove-image"),
    samples: $("#samples"),
    question: $("#question"),
    questionCount: $("#question-count"),
    profile: $("#profile"),
    detail: $("#detail"),
    apiKey: $("#api-key"),
    bypassCache: $("#bypass-cache"),
    askBtn: $("#ask-btn"),
    inputStatus: $("#input-status"),
    modelChip: $("#model-chip"),
    empty: $("#answer-empty"),
    shell: $("#answer-shell"),
    pipeline: $("#pipeline"),
    answer: $("#answer"),
    timing: $("#answer-timing"),
    typing: $("#typing"),
    metaImages: $("#meta-images"),
    metaVision: $("#meta-vision"),
    metaText: $("#meta-text"),
    metaCache: $("#meta-cache"),
    metaDegraded: $("#meta-degraded"),
    evidence: $("#evidence"),
    evidenceBody: $("#evidence-body"),
    error: $("#error"),
    errorText: $("#error-text"),
    copyCurl: $("#copy-curl"),
    curlCode: $("#curl-code"),
    toast: $("#toast"),
  };

  const state = {
    dataUrl: null,
    name: "",
    width: 0,
    height: 0,
    profile: "auto",
    detail: "standard",
    sampleSrc: null,
    running: false,
    controller: null,
    startedAt: 0,
    answerText: "",
    rendered: "",
    pendingFrame: false,
    toastTimer: 0,
  };

  const STEP_ORDER = ["image", "vision", "evidence", "answer"];
  const MAX_BYTES = 12 * 1024 * 1024;

  function setStatus(text, kind = "") {
    els.inputStatus.textContent = text;
    els.inputStatus.classList.toggle("is-error", kind === "error");
  }

  function showToast(text) {
    els.toast.textContent = text;
    els.toast.classList.add("is-visible");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => {
      els.toast.classList.remove("is-visible");
    }, 2400);
  }

  function updateQuestionCount() {
    els.questionCount.textContent = `${els.question.value.length} / 4000`;
  }

  function updateAskState() {
    els.askBtn.disabled = state.running || !state.dataUrl || !els.question.value.trim();
  }

  function setPipeline(active) {
    const index = STEP_ORDER.indexOf(active);
    $$(".pipe-step").forEach((step) => {
      const stepIndex = STEP_ORDER.indexOf(step.dataset.step);
      step.classList.toggle("is-active", stepIndex === index);
      step.classList.toggle("is-done", stepIndex < index);
    });
  }

  function finishPipeline() {
    $$(".pipe-step").forEach((step) => {
      step.classList.remove("is-active");
      step.classList.add("is-done");
    });
  }

  function formatMs(ms) {
    if (!Number.isFinite(ms) || ms <= 0) return "—";
    return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
  }

  function formatTokens(usage) {
    if (!usage) return "—";
    if (usage.total_tokens) return `${usage.total_tokens} tok`;
    const total = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
    return total ? `${total} tok` : "—";
  }

  function setText(el, value) {
    el.textContent = value;
  }

  async function loadHealth() {
    try {
      const response = await fetch("/healthz", { cache: "no-store" });
      if (!response.ok) throw new Error("health unavailable");
      const data = await response.json();
      const isMock = Boolean(data.mock);
      els.health.classList.toggle("is-mock", isMock);
      els.health.classList.toggle("is-live", !isMock);
      els.modeText.textContent = isMock ? "offline mock" : "live providers";
      els.health.title = isMock
        ? "Deterministic mock providers: no API keys or network calls."
        : "Live vision and text providers configured.";
      els.sampleNote.textContent = isMock ? "mock-friendly" : "synthetic samples";
    } catch {
      els.modeText.textContent = "mode unknown";
    }

    try {
      const response = await fetch("/v1/dualsight/info", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      const model = data?.text?.model || "dualsight";
      setText(els.modelChip, model);
      els.modelChip.title = `${data?.text?.provider || "provider"} · ${model}`;
    } catch {
      /* instance info is optional */
    }
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("Could not decode that image."));
      image.src = src;
    });
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error("Could not read that file."));
      reader.readAsDataURL(file);
    });
  }

  async function prepareFile(file) {
    if (!file.type.startsWith("image/")) {
      throw new Error("That file is not an image.");
    }
    if (file.size > MAX_BYTES) {
      throw new Error("That image is larger than the 12 MB limit.");
    }

    const original = await readFileAsDataUrl(file);
    const image = await loadImage(original);
    const width = image.naturalWidth || image.width;
    const height = image.naturalHeight || image.height;
    if (!width || !height) throw new Error("That image has no readable dimensions.");

    const maxDimension = 1600;
    const needsResize = width > maxDimension || height > maxDimension;
    const needsShrink = file.size > 3.5 * 1024 * 1024;
    if (!needsResize && !needsShrink) {
      return { dataUrl: original, width, height, name: file.name };
    }

    const scale = Math.min(1, maxDimension / Math.max(width, height));
    const targetW = Math.max(1, Math.round(width * scale));
    const targetH = Math.max(1, Math.round(height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = targetW;
    canvas.height = targetH;
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, targetW, targetH);
    context.imageSmoothingQuality = "high";
    context.drawImage(image, 0, 0, targetW, targetH);

    let quality = 0.88;
    let dataUrl = canvas.toDataURL("image/jpeg", quality);
    while (dataUrl.length > 9 * 1024 * 1024 && quality > 0.55) {
      quality -= 0.08;
      dataUrl = canvas.toDataURL("image/jpeg", quality);
    }
    return { dataUrl, width: targetW, height: targetH, name: file.name };
  }

  function markActiveSample(src) {
    $$(".sample").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.src === src);
    });
  }

  async function setFile(file, options = {}) {
    setStatus("Reading image…");
    const prepared = await prepareFile(file);
    state.dataUrl = prepared.dataUrl;
    state.name = options.name || prepared.name || "image";
    state.width = prepared.width;
    state.height = prepared.height;
    state.sampleSrc = options.sampleSrc || null;

    els.previewImg.src = prepared.dataUrl;
    els.preview.hidden = false;
    els.dropEmpty.hidden = true;
    setText(els.previewName, state.name);
    setText(els.previewMeta, `${prepared.width} × ${prepared.height}`);

    if (options.profile) {
      els.profile.value = options.profile;
    }
    if (typeof options.question === "string") {
      els.question.value = options.question;
    }
    updateQuestionCount();
    markActiveSample(state.sampleSrc);
    updateAskState();
    els.shell.hidden = true;
    els.empty.hidden = false;
    setStatus(options.status || "Image ready. Ask a question, or just run it.");
  }

  function clearFile() {
    state.dataUrl = null;
    state.name = "";
    state.width = 0;
    state.height = 0;
    state.sampleSrc = null;
    els.previewImg.removeAttribute("src");
    els.preview.hidden = true;
    els.dropEmpty.hidden = false;
    els.answerEmpty.hidden = false;
    els.answerShell.hidden = true;
    markActiveSample(null);
    updateAskState();
    setStatus("Choose an image to begin.");
  }

  async function loadSample(button, { silent = false } = {}) {
    const src = button.dataset.src;
    if (!src) return;
    try {
      if (!silent) setStatus("Loading sample…");
      const response = await fetch(src, { cache: "force-cache" });
      if (!response.ok) throw new Error("Sample image is unavailable.");
      const blob = await response.blob();
      const name = src.split("/").pop() || "sample.png";
      const file = new File([blob], name, { type: blob.type || "image/png" });
      await setFile(file, {
        name,
        sampleSrc: src,
        profile: button.dataset.profile || "auto",
        question: button.dataset.question || "",
        status: silent
          ? "Sample loaded. Run it, or ask your own question."
          : `Loaded ${name.replace("sample-", "").replace(".png", "")} sample.`,
      });
    } catch (error) {
      setStatus(error.message || "Could not load the sample.", "error");
      if (!silent) showToast(error.message || "Could not load the sample.");
    }
  }

  function renderInline(text) {
    const fragment = document.createDocumentFragment();
    const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) {
        fragment.append(document.createTextNode(text.slice(cursor, match.index)));
      }
      const token = match[0];
      if (token.startsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        fragment.append(strong);
      } else {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        fragment.append(code);
      }
      cursor = match.index + token.length;
    }
    if (cursor < text.length) fragment.append(document.createTextNode(text.slice(cursor)));
    return fragment;
  }

  function renderAnswer(text, force = false) {
    if (!force && text === state.rendered) return;
    state.rendered = text;
    els.answer.replaceChildren();

    const blocks = text.split(/\n{2,}/);
    for (const block of blocks) {
      if (!block.trim()) continue;
      const lines = block.split("\n");
      const isList = lines.every((line) => /^\s*[-*]\s+/.test(line));
      if (isList) {
        const ul = document.createElement("ul");
        ul.style.margin = "0 0 8px";
        ul.style.paddingLeft = "18px";
        for (const line of lines) {
          const li = document.createElement("li");
          li.append(renderInline(line.replace(/^\s*[-*]\s+/, "")));
          ul.append(li);
        }
        els.answer.append(ul);
      } else {
        const paragraph = document.createElement("p");
        lines.forEach((line, index) => {
          if (index > 0) paragraph.append(document.createElement("br"));
          paragraph.append(renderInline(line));
        });
        els.answer.append(paragraph);
      }
    }
  }

  function scheduleAnswerRender() {
    if (state.pendingFrame) return;
    state.pendingFrame = true;
    requestAnimationFrame(() => {
      state.pendingFrame = false;
      renderAnswer(state.answerText);
      els.answer.scrollTop = els.answer.scrollHeight;
    });
  }

  function evidenceSection(title, node) {
    const section = document.createElement("section");
    section.className = "evidence-section";
    const heading = document.createElement("h4");
    heading.textContent = title;
    section.append(heading, node);
    return section;
  }

  function textList(items, className) {
    const list = document.createElement("ul");
    list.className = className;
    for (const item of items) {
      const li = document.createElement("li");
      li.textContent = typeof item === "string" ? item : JSON.stringify(item);
      list.append(li);
    }
    return list;
  }

  function renderPerception(trace) {
    const fragment = document.createDocumentFragment();
    const p = trace.perception;

    if (trace.error) {
      const error = document.createElement("p");
      error.className = "evidence-summary";
      error.style.color = "#fda4af";
      error.textContent = `Vision unavailable: ${trace.error}`;
      fragment.append(error);
      return fragment;
    }

    if (!p) {
      const empty = document.createElement("p");
      empty.className = "evidence-placeholder";
      empty.textContent = "No structured perception was returned for this image.";
      fragment.append(empty);
      return fragment;
    }

    const summary = document.createElement("p");
    summary.className = "evidence-summary";
    summary.textContent = p.summary || "No summary returned.";
    fragment.append(summary);

    const ocr = Array.isArray(p.ocr) ? p.ocr : [];
    if (ocr.length) {
      const list = document.createElement("ul");
      list.className = "ocr-list";
      for (const span of ocr) {
        const li = document.createElement("li");
        const text = typeof span === "string" ? span : span.text || "";
        const confidence =
          typeof span === "object" && span && span.confidence
            ? ` · ${Math.round(Number(span.confidence) * 100)}%`
            : "";
        li.textContent = `${text}${confidence}`;
        list.append(li);
      }
      fragment.append(evidenceSection("verbatim OCR", list));
    }

    const objects = Array.isArray(p.objects) ? p.objects : [];
    if (objects.length) {
      const list = document.createElement("ul");
      list.className = "object-list";
      for (const object of objects) {
        if (typeof object === "string") {
          const li = document.createElement("li");
          li.textContent = object;
          list.append(li);
          continue;
        }
        const li = document.createElement("li");
        const name = object.name || "object";
        const count = object.count && object.count > 1 ? ` ×${object.count}` : "";
        const details = object.details ? ` — ${object.details}` : "";
        li.textContent = `${name}${count}${details}`;
        list.append(li);
      }
      fragment.append(evidenceSection("objects", list));
    }

    if (p.layout) {
      const layout = document.createElement("p");
      layout.className = "evidence-summary";
      layout.style.marginTop = "8px";
      layout.textContent = p.layout;
      fragment.append(evidenceSection("layout", layout));
    }

    if (Array.isArray(p.colors) && p.colors.length) {
      const colors = document.createElement("p");
      colors.className = "evidence-summary";
      colors.style.marginTop = "8px";
      colors.textContent = p.colors.join(" · ");
      fragment.append(evidenceSection("colors", colors));
    }

    if (p.chart && typeof p.chart === "object") {
      const chart = document.createElement("p");
      chart.className = "evidence-summary";
      chart.style.marginTop = "8px";
      const pieces = [];
      if (p.chart.type) pieces.push(`${p.chart.type} chart`);
      if (p.chart.axes) pieces.push(p.chart.axes);
      if (Array.isArray(p.chart.values)) pieces.push(`values: ${p.chart.values.join(", ")}`);
      if (p.chart.takeaway) pieces.push(p.chart.takeaway);
      chart.textContent = pieces.join(" · ");
      fragment.append(evidenceSection("chart", chart));
    }

    if (Array.isArray(p.uncertainties) && p.uncertainties.length) {
      fragment.append(evidenceSection("uncertainties", textList(p.uncertainties, "uncertainty-list")));
    }

    if (Array.isArray(p.suggested_questions) && p.suggested_questions.length) {
      const wrap = document.createElement("div");
      wrap.style.display = "flex";
      wrap.style.flexWrap = "wrap";
      wrap.style.gap = "5px";
      wrap.style.marginTop = "8px";
      for (const question of p.suggested_questions.slice(0, 4)) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = question;
        button.style.cssText =
          "padding:4px 7px;border:1px solid rgba(148,180,230,.16);border-radius:6px;" +
          "background:rgba(103,232,249,.06);color:#9fb4d4;font:9px var(--mono);cursor:pointer;" +
          "text-align:left";
        button.addEventListener("click", () => {
          els.question.value = question;
          updateQuestionCount();
          updateAskState();
          els.question.focus();
        });
        wrap.append(button);
      }
      fragment.append(evidenceSection("try asking", wrap));
    }

    return fragment;
  }

  function renderEvidence(meta) {
    els.evidenceBody.replaceChildren();
    const images = Array.isArray(meta?.images) ? meta.images : [];
    if (!images.length) {
      const placeholder = document.createElement("p");
      placeholder.className = "evidence-placeholder";
      placeholder.textContent = "No image trace was returned by the server.";
      els.evidenceBody.append(placeholder);
      return;
    }

    for (const trace of images) {
      const head = document.createElement("div");
      head.style.cssText =
        "display:flex;align-items:center;justify-content:space-between;gap:8px;" +
        "padding-top:10px;color:#64789a;font:8px var(--mono);text-transform:uppercase;" +
        "letter-spacing:.06em";
      const label = document.createElement("span");
      label.textContent = `${trace.label || trace.id} · ${trace.profile}`;
      const badges = document.createElement("span");
      badges.textContent = [
        trace.cached ? "cache hit" : "fresh",
        trace.error ? "error" : `${trace.latency_ms}ms`,
      ].join(" · ");
      head.append(label, badges);
      els.evidenceBody.append(head, renderPerception(trace));
    }
  }

  function setMeta(meta, usage, answerLatencyMs) {
    if (meta) {
      const images = Array.isArray(meta.images) ? meta.images : [];
      setText(els.metaImages, String(meta.image_count || images.length || 1));
      const first = images[0];
      if (first) {
        setText(els.metaVision, first.error ? "unavailable" : formatMs(first.latency_ms));
        els.metaVision.className = first.error ? "is-bad" : "";
        const cachedCount = images.filter((image) => image.cached).length;
        setText(
          els.metaCache,
          first.error ? "n/a" : cachedCount === images.length ? "hit" : "miss"
        );
        els.metaCache.className =
          !first.error && cachedCount === images.length ? "is-good" : "";
      }
      setText(els.metaDegraded, meta.degraded ? "yes" : "no");
      els.metaDegraded.className = meta.degraded ? "is-warn" : "";
      renderEvidence(meta);
      if (first) {
        const visionMs = first.latency_ms;
        const textMs = answerLatencyMs;
        setText(
          els.timing,
          `${formatMs(visionMs)} vision · ${formatMs(textMs)} answer`
        );
      }
    }
    if (usage) setText(els.metaText, formatTokens(usage));
    else if (!meta) setText(els.metaText, "—");
  }

  function resetOutput() {
    state.answerText = "";
    state.rendered = "";
    els.answer.replaceChildren();
    els.empty.hidden = true;
    els.shell.hidden = false;
    els.error.hidden = true;
    els.errorText.textContent = "";
    els.typing.hidden = false;
    els.evidence.open = true;
    els.evidenceBody.replaceChildren();
    const placeholder = document.createElement("p");
    placeholder.className = "evidence-placeholder";
    placeholder.textContent = "Perception pass in progress…";
    els.evidenceBody.append(placeholder);
    setText(els.metaImages, "—");
    setText(els.metaVision, "—");
    setText(els.metaText, "—");
    setText(els.metaCache, "—");
    setText(els.metaDegraded, "no");
    setText(els.timing, "running…");
    els.metaVision.className = "";
    els.metaCache.className = "";
    els.metaDegraded.className = "";
    setPipeline("image");
  }

  function buildRequestBody() {
    return {
      model: "dualsight",
      stream: true,
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: els.question.value.trim() },
            {
              type: "image_url",
              image_url: {
                url: state.dataUrl,
                detail: state.detail === "brief" ? "low" : "high",
              },
            },
          ],
        },
      ],
      dualsight: {
        profile: els.profile.value,
        detail: state.detail,
        bypass_cache: els.bypassCache.checked,
        include_perception: true,
      },
    };
  }

  async function readError(response) {
    try {
      const data = await response.json();
      return data?.error?.message || `Request failed (HTTP ${response.status}).`;
    } catch {
      return `Request failed (HTTP ${response.status}).`;
    }
  }

  async function run() {
    if (state.running) return;
    if (!state.dataUrl) {
      setStatus("Choose an image first.", "error");
      return;
    }
    const question = els.question.value.trim();
    if (!question) {
      setStatus("Ask a question about the image.", "error");
      els.question.focus();
      return;
    }

    state.running = true;
    state.startedAt = performance.now();
    state.controller = new AbortController();
    state.detail = $$("#detail button").find((button) => button.classList.contains("is-active"))
      ?.dataset.value || "standard";
    els.askBtn.classList.add("is-loading");
    els.askBtn.disabled = true;
    els.preview.classList.add("is-running");
    resetOutput();
    setPipeline("vision");
    setStatus("Vision pass running. The first token may take a moment…");

    const headers = { "Content-Type": "application/json" };
    const key = els.apiKey.value.trim();
    if (key) headers.Authorization = `Bearer ${key}`;

    let meta = null;
    let usage = null;
    let firstTextAt = 0;
    let finished = false;

    try {
      const response = await fetch("/v1/chat/completions", {
        method: "POST",
        headers,
        body: JSON.stringify(buildRequestBody()),
        signal: state.controller.signal,
      });
      if (!response.ok) throw new Error(await readError(response));
      if (!response.body) {
        const data = await response.json();
        state.answerText = data?.choices?.[0]?.message?.content || "";
        meta = data?.dualsight || null;
        usage = data?.usage || null;
        renderAnswer(state.answerText, true);
        els.typing.hidden = true;
        setMeta(meta, usage, performance.now() - state.startedAt);
        finishPipeline();
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (!block.trim()) continue;

          const dataLines = block
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart());
          if (!dataLines.length) continue;
          const payloadText = dataLines.join("\n");
          if (payloadText === "[DONE]") {
            finished = true;
            break;
          }

          let payload;
          try {
            payload = JSON.parse(payloadText);
          } catch {
            continue;
          }

          if (payload.error) {
            throw new Error(payload.error.message || "The stream returned an error.");
          }

          if (payload.dualsight) {
            meta = payload.dualsight;
            renderEvidence(meta);
          }
          if (payload.usage) {
            usage = payload.usage;
          }

          const choice = payload.choices?.[0];
          if (!choice) continue;
          const delta = choice.delta || {};
          if (typeof delta.content === "string" && delta.content) {
            if (!firstTextAt) {
              firstTextAt = performance.now();
              setPipeline("evidence");
              els.typing.hidden = false;
            }
            state.answerText += delta.content;
            scheduleAnswerRender();
          }
          if (choice.finish_reason) {
            setPipeline("answer");
          }
        }
        if (finished) break;
      }

      if (!state.answerText.trim()) {
        state.answerText = "(The model returned no text. Check the evidence and server logs.)";
      }
      renderAnswer(state.answerText, true);
      els.typing.hidden = true;
      const answerLatency = firstTextAt ? performance.now() - firstTextAt : performance.now() - state.startedAt;
      setMeta(meta, usage, answerLatency);
      finishPipeline();
      const total = performance.now() - state.startedAt;
      setText(els.timing, `${formatMs(total)} total`);
      setStatus(
        `Done in ${formatMs(total)}. ${usage?.total_tokens ? `${usage.total_tokens} text tokens.` : "Stream complete."}`
      );
      if (meta?.degraded) showToast("Vision degraded — the answer used a fallback evidence note.");
    } catch (error) {
      if (error.name === "AbortError") {
        setStatus("Request cancelled.");
      } else {
        const message = error.message || "Something went wrong.";
        els.error.hidden = false;
        els.errorText.textContent = message;
        els.typing.hidden = true;
        setText(els.timing, "failed");
        setStatus(message, "error");
        const active = $(".pipe-step.is-active");
        if (active) active.classList.remove("is-active");
      }
    } finally {
      state.running = false;
      state.controller = null;
      els.askBtn.classList.remove("is-loading");
      els.preview.classList.remove("is-running");
      updateAskState();
    }
  }

  function currentCurl() {
    const question = els.question.value.trim() || "What is in this image?";
    return [
      "curl http://127.0.0.1:8000/v1/chat/completions \\",
      '  -H "Content-Type: application/json" \\',
      "  -d '{",
      '    "model": "dualsight",',
      '    "messages": [{"role": "user", "content": [',
      `      {"type": "text", "text": ${JSON.stringify(question)}},`,
      '      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}',
      "    ]}],",
      '    "dualsight": {"profile": "' + els.profile.value + '", "detail": "' + state.detail + '"}',
      "  }'",
    ].join("\n");
  }

  function bindEvents() {
    els.dropzone.addEventListener("click", (event) => {
      if (event.target.closest("#remove-image")) return;
      els.fileInput.click();
    });

    els.dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        els.fileInput.click();
      }
    });

    els.dropzone.setAttribute("tabindex", "0");
    els.dropzone.setAttribute("role", "button");
    els.dropzone.setAttribute("aria-label", "Choose or drop an image");

    els.fileInput.addEventListener("change", async () => {
      const file = els.fileInput.files?.[0];
      if (!file) return;
      try {
        await setFile(file, { status: "Image ready. Ask a question, or just run it." });
      } catch (error) {
        setStatus(error.message, "error");
        showToast(error.message);
      } finally {
        els.fileInput.value = "";
      }
    });

    els.removeImage.addEventListener("click", (event) => {
      event.stopPropagation();
      clearFile();
    });

    ["dragenter", "dragover"].forEach((type) => {
      els.dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        els.dropzone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((type) => {
      els.dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        els.dropzone.classList.remove("is-dragging");
      });
    });
    els.dropzone.addEventListener("drop", async (event) => {
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      try {
        await setFile(file);
      } catch (error) {
        setStatus(error.message, "error");
        showToast(error.message);
      }
    });

    document.addEventListener("paste", async (event) => {
      const items = Array.from(event.clipboardData?.items || []);
      const item = items.find((entry) => entry.type.startsWith("image/"));
      if (!item) return;
      const file = item.getAsFile();
      if (!file) return;
      event.preventDefault();
      try {
        await setFile(file, { name: "pasted-image.png" });
        showToast("Pasted image from clipboard.");
      } catch (error) {
        setStatus(error.message, "error");
        showToast(error.message);
      }
    });

    els.samples.addEventListener("click", (event) => {
      const button = event.target.closest(".sample");
      if (button) loadSample(button);
    });

    els.question.addEventListener("input", () => {
      updateQuestionCount();
      updateAskState();
    });

    els.question.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        run();
      }
    });

    els.profile.addEventListener("change", () => {
      state.profile = els.profile.value;
      markActiveSample(null);
    });

    els.detail.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-value]");
      if (!button) return;
      $$("#detail button").forEach((item) => {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-checked", String(active));
      });
      state.detail = button.dataset.value;
    });

    els.askBtn.addEventListener("click", run);

    els.copyCurl.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(currentCurl());
        els.copyCurl.textContent = "Copied";
        showToast("cURL request copied.");
        window.setTimeout(() => {
          els.copyCurl.textContent = "Copy cURL";
        }, 1600);
      } catch {
        showToast("Clipboard access is unavailable.");
      }
    });

    els.apiKey.addEventListener("input", () => {
      try {
        if (els.apiKey.value.trim()) {
          sessionStorage.setItem("dualsight_api_key", els.apiKey.value.trim());
        } else {
          sessionStorage.removeItem("dualsight_api_key");
        }
      } catch {
        /* storage may be disabled */
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.controller) {
        state.controller.abort();
      }
    });
  }

  async function init() {
    bindEvents();
    updateQuestionCount();
    updateAskState();
    loadHealth();

    try {
      const savedKey = sessionStorage.getItem("dualsight_api_key");
      if (savedKey) els.apiKey.value = savedKey;
    } catch {
      /* storage may be disabled */
    }

    const defaultSample = $('.sample[data-src="/assets/samples/sample-card.png"]');
    if (defaultSample) {
      await loadSample(defaultSample, { silent: true });
    } else {
      setStatus("Choose an image to begin.");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
