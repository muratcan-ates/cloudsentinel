/* ======================================================================
 * CloudSentinel — orchestration console (static/chat.html)
 * ----------------------------------------------------------------------
 * One operator, one seat, one question: POST /chat {agent, question} and
 * render what comes back —
 *
 *   {agent, answer, evidence: [...], table?: {columns: [], rows: [[]]},
 *    model, source: "gemini" | "fake" | "fallback"}
 *
 * Two rules run through the whole file.
 *
 * 1. Nothing from the network is ever written as markup. Every value the
 *    backend returns lands on the page through textContent or a text node,
 *    so an answer, an evidence row or a table cell cannot author an element
 *    here. That is belt and braces on top of the app's script-src 'self'
 *    CSP, not a substitute for it.
 *
 * 2. The console never covers for the backend. If /chat is missing, refuses,
 *    times out or answers something this file cannot read, the transcript
 *    says exactly that, in the operator's own words, with the status code.
 *    It never writes a plausible-looking answer of its own, and it never
 *    stamps a source badge on anything the backend did not stamp itself.
 *
 * No dependencies, no build step, no external host.
 * ====================================================================== */

(function () {
  "use strict";

  var ENDPOINT = "/chat";
  var TIMEOUT_MS = 45000;

  /* How a stated source is shown. The label is the badge's word, the note is
   * its tooltip. A source outside this table is reported verbatim rather than
   * quietly mapped onto one of these — mislabelling provenance is the one
   * mistake this product cannot afford. */
  var SOURCES = {
    /* The backend's word for a real model call is "gemini", not "live":
     * app/llm.py types it Literal["gemini", "fake", "fallback"] and every
     * agent passes that straight through. "live" is kept as an alias so a
     * future provider that says so is not badged as unrecognised — but
     * without the real word, every genuinely live answer would have landed
     * in the red alert lane, inches from a legend advertising "live". */
    gemini: {
      lane: "market",
      label: "live",
      note: "A model was called and answered."
    },
    live: {
      lane: "market",
      label: "live",
      note: "A model was called and answered."
    },
    fake: {
      lane: "proof",
      label: "demo",
      note: "The deterministic demo lane — no provider was called."
    },
    fallback: {
      lane: "ops",
      label: "fallback",
      note: "The model was unreachable; the rule-based fallback answered."
    }
  };

  var SEAT_LABEL = {
    analyst: "asking the analyst",
    recommender: "asking the recommender",
    skeptic: "asking the skeptic",
    chronicler: "asking the chronicler"
  };

  var dom = {};
  var options = [];
  var agent = "analyst";
  var busy = false;

  /* ---------------------------------------------------------------- utils */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function isPlainText(value) {
    return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
  }

  /* A cell or an evidence line, rendered honestly: a blank stays a dash, a
   * structure this console did not expect is shown as its own JSON rather
   * than dropped, so nothing the backend sent goes missing from the screen. */
  function asText(value) {
    if (value === null || value === undefined || value === "") { return "—"; }
    if (isPlainText(value)) { return String(value); }
    try {
      return JSON.stringify(value);
    } catch (err) {
      return String(value);
    }
  }

  function pick(obj, keys) {
    for (var i = 0; i < keys.length; i += 1) {
      var value = obj[keys[i]];
      if (isPlainText(value) && String(value) !== "") { return String(value); }
    }
    return "";
  }

  /* Evidence arrives either as plain strings or as small records. Both are
   * flattened to one readable line; anything unrecognised falls through to
   * its JSON so the citation is never silently emptied. */
  function evidenceLine(item) {
    if (item === null || item === undefined) { return { mark: "", text: "—" }; }
    if (isPlainText(item)) { return { mark: "", text: String(item) }; }
    if (typeof item !== "object") { return { mark: "", text: String(item) }; }

    var mark = pick(item, ["id", "ref", "row", "row_id", "code"]);
    var body = pick(item, ["text", "detail", "note", "summary", "description", "label", "title", "name", "reason", "value"]);
    if (!body) {
      var rest = {};
      var hasRest = false;
      Object.keys(item).forEach(function (key) {
        if (mark && (key === "id" || key === "ref" || key === "row" || key === "row_id" || key === "code")) { return; }
        rest[key] = item[key];
        hasRest = true;
      });
      body = hasRest ? asText(rest) : "—";
    }
    return { mark: mark, text: body };
  }

  function scrollTranscript() {
    dom.transcript.scrollTop = dom.transcript.scrollHeight;
  }

  function hideEmptyNote() {
    if (dom.empty && dom.empty.parentNode) {
      dom.empty.parentNode.removeChild(dom.empty);
    }
  }

  /* ------------------------------------------------------------- messages */

  function addMessage(kind, whoText) {
    hideEmptyNote();
    var wrap = el("article", "msg msg-" + kind);
    var bubble = el("div", "msg-bubble");
    if (whoText) { bubble.appendChild(el("p", "msg-who", whoText)); }
    wrap.appendChild(bubble);
    dom.transcript.appendChild(wrap);
    scrollTranscript();
    return bubble;
  }

  function addOperatorMessage(question) {
    var bubble = addMessage("you", "operator");
    bubble.appendChild(el("p", "msg-text", question));
    scrollTranscript();
  }

  function addPending(forAgent) {
    var bubble = addMessage("agent", forAgent);
    bubble.parentNode.className = "msg msg-agent msg-pending";
    var line = el("p", "msg-text", "thinking");
    var dots = el("span", "msg-dots");
    dots.appendChild(el("i"));
    dots.appendChild(el("i"));
    dots.appendChild(el("i"));
    line.appendChild(dots);
    bubble.appendChild(line);
    scrollTranscript();
    return bubble.parentNode;
  }

  /* A console note: the backend could not be reached, or answered something
   * that is not an answer. Deliberately not an agent bubble and deliberately
   * without a source badge — this is the console talking about itself. */
  function replaceWithNote(node, lines) {
    node.className = "msg msg-note";
    var bubble = node.firstChild;
    while (bubble.firstChild) { bubble.removeChild(bubble.firstChild); }
    bubble.appendChild(el("p", "msg-who", "console"));
    lines.forEach(function (line) {
      bubble.appendChild(el("p", "msg-text", line));
    });
    scrollTranscript();
  }

  function sourceBadge(source) {
    var known = Object.prototype.hasOwnProperty.call(SOURCES, source) ? SOURCES[source] : null;
    if (known) {
      var badge = el("span", "cs-badge", known.label);
      badge.setAttribute("data-lane", known.lane);
      badge.setAttribute("title", known.note);
      return badge;
    }
    var stated = isPlainText(source) && String(source) !== "";
    var present = source !== null && source !== undefined && source !== "";
    var label = stated ? String(source) : (present ? "source unreadable" : "source not stated");
    var odd = el("span", "cs-badge", label);
    odd.setAttribute("data-lane", "security");
    odd.setAttribute(
      "title",
      stated
        ? "The backend reported a source this console does not recognise. It is shown exactly as sent."
        : (present
          ? "The reply carried a source field this console could not read, so it will not translate it into one of the three it knows."
          : "The reply carried no source field, so this console will not claim one.")
    );
    return odd;
  }

  function renderEvidence(bubble, evidence) {
    if (!Array.isArray(evidence) || evidence.length === 0) { return; }
    var box = el("div", "evidence");
    box.appendChild(el("p", "evidence-head", "evidence cited"));
    var list = el("ul", "evidence-list");
    evidence.forEach(function (item, index) {
      var line = evidenceLine(item);
      var li = el("li", "evidence-item");
      li.appendChild(el("span", "evidence-mark", line.mark || "E" + (index + 1)));
      li.appendChild(el("span", "evidence-text", line.text));
      list.appendChild(li);
    });
    box.appendChild(list);
    bubble.appendChild(box);
  }

  function renderTable(bubble, table) {
    if (!table || typeof table !== "object") { return; }
    var columns = Array.isArray(table.columns) ? table.columns : [];
    var rows = Array.isArray(table.rows) ? table.rows : [];
    if (columns.length === 0 && rows.length === 0) { return; }

    var wrap = el("figure", "tbl-wrap");
    var tbl = el("table", "tbl");
    var caption = el("caption", null, "table returned with this answer");
    tbl.appendChild(caption);

    var thead = el("thead");
    var headRow = el("tr");
    columns.forEach(function (column) {
      var th = el("th", null, asText(column));
      th.setAttribute("scope", "col");
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    tbl.appendChild(thead);

    var tbody = el("tbody");
    rows.forEach(function (row) {
      var cells = Array.isArray(row) ? row : [row];
      var tr = el("tr");
      var width = Math.max(cells.length, columns.length);
      for (var i = 0; i < width; i += 1) {
        tr.appendChild(el("td", null, i < cells.length ? asText(cells[i]) : "—"));
      }
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);

    wrap.appendChild(tbl);
    bubble.appendChild(wrap);
    bubble.className = "msg-bubble has-table";
  }

  function renderAnswer(node, payload, askedAgent) {
    node.className = "msg msg-agent";
    var bubble = node.firstChild;
    while (bubble.firstChild) { bubble.removeChild(bubble.firstChild); }

    var head = el("div", "msg-head");
    var who = isPlainText(payload.agent) && String(payload.agent) !== "" ? String(payload.agent) : askedAgent;
    head.appendChild(el("span", "msg-who", who));
    head.appendChild(sourceBadge(payload.source));
    if (isPlainText(payload.model) && String(payload.model) !== "") {
      head.appendChild(el("span", "msg-model", String(payload.model)));
    }
    bubble.appendChild(head);

    bubble.appendChild(el("p", "msg-text", payload.answer));

    if (who !== askedAgent) {
      bubble.appendChild(el(
        "p",
        "msg-text",
        "Note: you asked the " + askedAgent + "; the backend says this answer came from the " + who + "."
      ));
    }

    renderEvidence(bubble, payload.evidence);
    renderTable(bubble, payload.table);
    scrollTranscript();
  }

  /* ------------------------------------------------------------ the picker */

  function selectAgent(next, moveFocus) {
    agent = next;
    options.forEach(function (option) {
      var chosen = option.getAttribute("data-agent") === next;
      option.setAttribute("aria-checked", chosen ? "true" : "false");
      option.tabIndex = chosen ? 0 : -1;
      if (chosen && moveFocus) { option.focus(); }
    });
    dom.seat.textContent = SEAT_LABEL[next] || ("asking the " + next);
  }

  function wirePicker() {
    options.forEach(function (option, index) {
      option.addEventListener("click", function () {
        selectAgent(option.getAttribute("data-agent"), false);
      });
      option.addEventListener("keydown", function (event) {
        var step = 0;
        if (event.key === "ArrowDown" || event.key === "ArrowRight") { step = 1; }
        else if (event.key === "ArrowUp" || event.key === "ArrowLeft") { step = -1; }
        else if (event.key === "Home") { step = -index; }
        else if (event.key === "End") { step = options.length - 1 - index; }
        else if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          selectAgent(option.getAttribute("data-agent"), true);
          return;
        } else { return; }
        event.preventDefault();
        var target = (index + step + options.length) % options.length;
        selectAgent(options[target].getAttribute("data-agent"), true);
      });
    });
  }

  /* --------------------------------------------------------- the backend */

  /* A free, honest capability probe: the published API surface either lists
   * POST /chat or it does not. It spends no quota and calls no model — and
   * when it cannot be read, the rail says "unknown" rather than guessing. */
  function probeBackend() {
    fetch("/openapi.json", { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) { throw new Error("status " + res.status); }
        return res.json();
      })
      .then(function (spec) {
        var paths = spec && spec.paths ? spec.paths : {};
        var route = Object.prototype.hasOwnProperty.call(paths, ENDPOINT) ? paths[ENDPOINT] : null;
        if (route && route.post) {
          setBackend("present", "is-live", "Published on this build's API surface.");
        } else {
          setBackend("absent", "is-absent", "Not on this build yet — the console will say so rather than answer for it.");
        }
      })
      .catch(function () {
        setBackend("unknown", "", "Could not read /openapi.json; the first question will settle it.");
      });
  }

  function setBackend(state, cls, note) {
    dom.backendState.textContent = state;
    dom.backendState.className = "cs-row-value" + (cls ? " " + cls : " is-off");
    dom.backendNote.textContent = note;
  }

  function statusNote(status, detail) {
    var head;
    if (status === 404) {
      head = "The console is waiting for its backend: POST " + ENDPOINT + " answered 404 on this build.";
    } else if (status === 405) {
      head = "POST " + ENDPOINT + " answered 405 — the path exists but will not take a POST.";
    } else if (status === 401 || status === 403) {
      head = "POST " + ENDPOINT + " answered " + status + " — the endpoint is there, but this session is not allowed to use it.";
    } else if (status === 422) {
      head = "POST " + ENDPOINT + " answered 422 — it rejected the shape of this request.";
    } else if (status === 429) {
      head = "POST " + ENDPOINT + " answered 429 — the model budget is spent for now.";
    } else if (status >= 500) {
      head = "POST " + ENDPOINT + " answered " + status + " — the backend failed while answering.";
    } else {
      head = "POST " + ENDPOINT + " answered " + status + ".";
    }
    var lines = [head];
    if (detail) { lines.push("It said: " + detail); }
    lines.push("Nothing was answered, so nothing is shown. This console does not invent one.");
    return lines;
  }

  /* Read whatever explanation the backend offered, without trusting its
   * shape — FastAPI's `detail` may be a string, a list or an object. */
  function readDetail(body) {
    if (!body || typeof body !== "object") { return ""; }
    if (isPlainText(body.detail)) { return String(body.detail); }
    if (isPlainText(body.message)) { return String(body.message); }
    if (body.detail !== undefined && body.detail !== null) { return asText(body.detail); }
    return "";
  }

  function setBusy(state) {
    busy = state;
    dom.send.disabled = state;
    dom.transcript.setAttribute("aria-busy", state ? "true" : "false");
  }

  function ask(question) {
    var askedAgent = agent;
    addOperatorMessage(question);
    var pending = addPending(askedAgent);
    setBusy(true);

    var controller = null;
    var timer = null;
    if (typeof AbortController === "function") {
      controller = new AbortController();
      timer = window.setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
    }

    var status = 0;
    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ agent: askedAgent, question: question }),
      signal: controller ? controller.signal : undefined
    })
      .then(function (res) {
        status = res.status;
        return res.text().then(function (text) {
          var parsed = null;
          var parseFailed = false;
          if (text) {
            try { parsed = JSON.parse(text); } catch (err) { parseFailed = true; }
          }
          return { ok: res.ok, body: parsed, parseFailed: parseFailed };
        });
      })
      .then(function (result) {
        if (timer) { window.clearTimeout(timer); }

        if (!result.ok) {
          replaceWithNote(pending, statusNote(status, readDetail(result.body)));
          if (status === 404) {
            setBackend("absent", "is-absent", "POST " + ENDPOINT + " answered 404 just now.");
          }
          return;
        }
        if (result.parseFailed || !result.body || typeof result.body !== "object") {
          replaceWithNote(pending, [
            "POST " + ENDPOINT + " answered " + status + ", but the body was not JSON this console could read.",
            "Nothing is shown, because showing a guess would be worse than showing nothing."
          ]);
          return;
        }
        if (!isPlainText(result.body.answer) || String(result.body.answer) === "") {
          replaceWithNote(pending, [
            "POST " + ENDPOINT + " answered " + status + ", but the reply carried no answer field.",
            "The console will not fill that gap itself."
          ]);
          return;
        }

        renderAnswer(pending, result.body, askedAgent);
        setBackend("present", "is-live", "It answered this question.");
      })
      .catch(function (err) {
        if (timer) { window.clearTimeout(timer); }
        if (err && err.name === "AbortError") {
          replaceWithNote(pending, [
            "The request to POST " + ENDPOINT + " ran past " + Math.round(TIMEOUT_MS / 1000) + " seconds and was given up on.",
            "It may still be running on the server; nothing came back here, so nothing is shown."
          ]);
          return;
        }
        replaceWithNote(pending, [
          "The console could not reach POST " + ENDPOINT + " at all — the request failed before any status came back.",
          "That is a network or server problem, not an answer."
        ]);
      })
      .then(function () {
        setBusy(false);
        dom.input.focus();
      });
  }

  function submit() {
    if (busy) { return; }
    var question = dom.input.value.trim();
    if (!question) {
      dom.input.focus();
      return;
    }
    dom.input.value = "";
    ask(question);
  }

  /* ------------------------------------------------------------- start-up */

  function init() {
    dom.transcript = document.getElementById("transcript");
    dom.empty = document.getElementById("transcript-empty");
    dom.input = document.getElementById("ask-input");
    dom.form = document.getElementById("ask-form");
    dom.send = document.getElementById("ask-send");
    dom.seat = document.getElementById("composer-seat");
    dom.clear = document.getElementById("transcript-clear");
    dom.backendState = document.getElementById("backend-state");
    dom.backendNote = document.getElementById("backend-note");

    if (!dom.transcript || !dom.input || !dom.form) { return; }

    options = Array.prototype.slice.call(document.querySelectorAll(".agent-opt"));
    wirePicker();
    selectAgent(agent, false);

    Array.prototype.forEach.call(document.querySelectorAll(".example-chip"), function (chip) {
      chip.addEventListener("click", function () {
        dom.input.value = chip.getAttribute("data-question") || "";
        dom.input.focus();
        dom.input.setSelectionRange(dom.input.value.length, dom.input.value.length);
      });
    });

    dom.form.addEventListener("submit", function (event) {
      event.preventDefault();
      submit();
    });

    /* Enter sends, Shift+Enter opens a line. An IME composition run is left
     * alone: committing a candidate with Enter must not fire the question. */
    dom.input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) { return; }
      if (event.isComposing || event.keyCode === 229) { return; }
      event.preventDefault();
      submit();
    });

    if (dom.clear) {
      dom.clear.addEventListener("click", function () {
        while (dom.transcript.firstChild) { dom.transcript.removeChild(dom.transcript.firstChild); }
        if (dom.empty) { dom.transcript.appendChild(dom.empty); }
        dom.input.focus();
      });
    }

    probeBackend();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
