const POLL_MS = 100;
const DISCONNECTED_AFTER_SECONDS = 15;
const csrfMeta = document.querySelector('meta[name="csrf-token"]');
const csrfToken = csrfMeta ? csrfMeta.content : "";

// Core Relay DOM Elements (Required for Live Feed)
const image = document.querySelector("#relay-image");
const placeholder = document.querySelector("#monitor-placeholder");
const badge = document.querySelector("#monitor-badge");
const detail = document.querySelector("#monitor-detail");

// Optional Left Panel Elements (Snapshot & Inventory Pills)
const analyzeBtn = document.querySelector("#monitor-analyze-btn");
const actionStatus = document.querySelector("#monitor-action-status");
const autoAnalyzeToggle = document.querySelector("#auto-analyze-toggle");
const autoScanBadge = document.querySelector("#auto-scan-badge");
const quickInvCount = document.querySelector("#quick-inv-count");
const quickInvPills = document.querySelector("#quick-inv-pills");

// Optional Right Panel Elements (Project Guidance & Copilot Chat)
const projectSelect = document.querySelector("#assistant-project-select");
const readinessBadge = document.querySelector("#assistant-readiness-badge");
const partsConfirmedText = document.querySelector("#context-parts-confirmed");
const partsMissingText = document.querySelector("#context-parts-missing");
const stepTitle = document.querySelector("#step-viewer-title");
const stepCounter = document.querySelector("#step-nav-counter");
const stepText = document.querySelector("#step-instruction-text");
const stepSafetyText = document.querySelector("#step-safety-text");
const prevStepBtn = document.querySelector("#prev-step-btn");
const nextStepBtn = document.querySelector("#next-step-btn");

// Assistant Build Guide Tabs
const tabBtnSteps = document.querySelector("#tab-btn-steps");
const tabBtnCircuit = document.querySelector("#tab-btn-circuit");
const tabBtnTrouble = document.querySelector("#tab-btn-trouble");
const tabPanelSteps = document.querySelector("#tab-panel-steps");
const tabPanelCircuit = document.querySelector("#tab-panel-circuit");
const tabPanelTrouble = document.querySelector("#tab-panel-trouble");

const monitorCircuitTitle = document.querySelector("#monitor-circuit-title");
const monitorCircuitStatusTag = document.querySelector("#monitor-circuit-status-tag");
const monitorCircuitTbody = document.querySelector("#monitor-circuit-tbody");
const monitorTroubleList = document.querySelector("#monitor-trouble-list");

const chatFeed = document.querySelector("#chat-feed");
const chatForm = document.querySelector("#chat-form");
const chatInput = document.querySelector("#chat-input");
const chatSendBtn = document.querySelector("#chat-send-btn");

let shownSeq = 0;
let objectUrl = null;
let pollFailures = 0;

const AUTO_ANALYZE_INTERVAL_MS = 6000; // Configurable cooldown between analyses
let autoAnalyzeEnabled = true;
let autoAnalyzeInFlight = false;
let lastAnalyzedSeq = 0;
let lastAnalysisTime = 0;
let autoAnalyzeTimer = null;
let currentRelayState = "waiting";

let projects = [];
let inventory = [];
let activeProjectId = null;
let currentStepIndex = 1; // 1-indexed
let chatInFlight = false;
const CHAT_TIMEOUT_MS = 20000; // 20-second client-side timeout for chat

// --- Left Panel: Live Relay Logic ------------------------------------------

function setBadge(state, text) {
  if (badge) {
    badge.dataset.state = state;
    badge.textContent = text;
  }
}

function showImage(visible) {
  if (image) image.hidden = !visible;
  if (placeholder) placeholder.hidden = visible;
  if (analyzeBtn) analyzeBtn.disabled = !visible;
}

function clearImage() {
  if (image) image.removeAttribute("src");
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
  shownSeq = 0;
  showImage(false);
}

async function loadLatestFrame() {
  const response = await fetch("/api/relay/frame", { cache: "no-store" });
  if (response.status === 404) return;
  if (!response.ok) throw new Error(`frame-http-${response.status}`);
  const seq = Number(response.headers.get("X-Relay-Seq")) || 0;
  const blob = await response.blob();
  const nextUrl = URL.createObjectURL(blob);
  const previousUrl = objectUrl;
  if (image) image.src = nextUrl;
  objectUrl = nextUrl;
  shownSeq = seq;
  showImage(true);
  if (previousUrl) URL.revokeObjectURL(previousUrl);
}

function renderRelayStatus(status) {
  currentRelayState = status.state;
  const age = status.age_seconds;
  if (status.state === "stopped") {
    clearImage();
    setBadge("stopped", "CAMERA STOPPED");
    if (detail) detail.textContent = "The phone stopped its camera. The last frame was discarded.";
  } else if (status.state === "waiting") {
    setBadge("waiting", "WAITING");
    if (detail) detail.textContent = "No frame has arrived yet. Start the camera on the phone.";
  } else if (age > DISCONNECTED_AFTER_SECONDS) {
    setBadge("disconnected", "PHONE DISCONNECTED?");
    if (detail) detail.textContent = `No new frame for ${Math.round(age)} s. The last frame is dimmed; phone may have lost connection.`;
  } else if (status.state === "stale") {
    setBadge("stale", "STALE");
    if (detail) detail.textContent = `No new frame for ${age.toFixed(1)} s. Showing the last frame received.`;
  } else {
    setBadge("live", "LIVE");
    const received = status.received_at ? new Date(status.received_at).toLocaleTimeString() : "";
    if (detail) detail.textContent = `Frame #${status.seq} · ${age.toFixed(1)} s old · received ${received}`;
  }
  if (image) image.classList.toggle("is-stale", status.state === "stale" || age > DISCONNECTED_AFTER_SECONDS);

  // Auto-scan lifecycle management
  if (status.state === "live") {
    if (autoAnalyzeEnabled && !autoAnalyzeInFlight) {
      maybeTriggerAutoAnalyze();
    }
  } else {
    if (autoScanBadge && !autoAnalyzeInFlight) {
      autoScanBadge.textContent = `Auto-scan paused (${status.state})`;
      autoScanBadge.className = "auto-scan-badge is-paused";
    }
  }
}

async function pollRelay() {
  let delay = POLL_MS;
  try {
    const response = await fetch("/api/relay/status", { cache: "no-store" });
    if (response.status === 401) {
      setBadge("disconnected", "SIGNED OUT");
      if (detail) detail.textContent = "Your session ended. Reload this page and sign in again.";
      return;
    }
    if (!response.ok) throw new Error(`status-http-${response.status}`);
    const status = await response.json();
    pollFailures = 0;
    if ((status.state === "live" || status.state === "stale") && status.seq !== shownSeq) {
      await loadLatestFrame();
    }
    renderRelayStatus(status);
  } catch (_error) {
    pollFailures += 1;
    delay = Math.min(5000, POLL_MS * 2 ** Math.min(pollFailures, 4));
    setBadge("disconnected", "NO CONTACT");
    if (detail) detail.textContent = "Cannot reach the BoardBuddy server from this page. Retrying…";
    if (image) image.classList.add("is-stale");
  }
  window.setTimeout(pollRelay, delay);
}

// Start relay polling immediately if core elements are present
if (image && placeholder && badge) {
  pollRelay();
} else {
  console.error("BoardBuddy: Core relay DOM elements (#relay-image, #monitor-placeholder, #monitor-badge) not found.");
}

// --- Left Panel: Controlled Automatic & Manual Frame Analysis -------------

async function performAnalysis(triggerSource = "manual") {
  if (autoAnalyzeInFlight || chatInFlight) return;
  if (!shownSeq) {
    if (actionStatus) actionStatus.textContent = "Wait for a live camera frame before scanning.";
    return;
  }

  autoAnalyzeInFlight = true;
  const targetSeq = shownSeq;
  lastAnalysisTime = Date.now();

  if (autoScanBadge) {
    autoScanBadge.textContent = "Analyzing…";
    autoScanBadge.className = "auto-scan-badge is-analyzing";
  }
  if (actionStatus) {
    actionStatus.textContent = `Analyzing frame #${targetSeq} with local Gemma 3 4B…`;
  }
  if (analyzeBtn) analyzeBtn.disabled = true;

  try {
    const frameRes = await fetch("/api/relay/frame", { cache: "no-store" });
    if (!frameRes.ok) throw new Error("Could not retrieve current frame.");
    const blob = await frameRes.blob();
    const formData = new FormData();
    formData.append("image", blob, `monitor-frame-${targetSeq}.jpg`);

    const analyzeRes = await fetch("/api/analyze", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: formData,
    });
    const analyzeData = await analyzeRes.json();
    if (!analyzeRes.ok) throw new Error(analyzeData.error || "Analysis failed.");

    lastAnalyzedSeq = targetSeq;
    const components = analyzeData.components || [];
    const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

    if (components.length === 0) {
      if (actionStatus) {
        actionStatus.textContent = `No components identified in frame #${targetSeq} (${timeStr}). Reposition or frame closer.`;
      }
      if (autoScanBadge) {
        autoScanBadge.textContent = "No parts identified";
        autoScanBadge.className = "auto-scan-badge is-idle";
      }
    } else {
      const invRes = await fetch("/api/inventory", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
        body: JSON.stringify({ items: components, source: "laptop monitor" }),
      });
      const invData = await invRes.json();
      await loadInventory();

      const createdCount = (invData.created || []).length;
      if (actionStatus) {
        if (createdCount > 0) {
          actionStatus.textContent = `Observed ${components.length} component(s) (${createdCount} new unconfirmed) at ${timeStr}.`;
        } else {
          actionStatus.textContent = `Observed ${components.length} known component(s) at ${timeStr}.`;
        }
      }
      if (autoScanBadge) {
        autoScanBadge.textContent = `Active (${components.length} parts)`;
        autoScanBadge.className = "auto-scan-badge is-active";
      }
    }
  } catch (err) {
    if (actionStatus) {
      actionStatus.textContent = `Analysis error: ${err.message}`;
    }
    if (autoScanBadge) {
      autoScanBadge.textContent = "Last scan failed";
      autoScanBadge.className = "auto-scan-badge is-error";
    }
  } finally {
    autoAnalyzeInFlight = false;
    if (analyzeBtn) analyzeBtn.disabled = !shownSeq;
    // Schedule next automatic scan check after cooldown
    if (autoAnalyzeEnabled && currentRelayState === "live") {
      if (autoAnalyzeTimer) window.clearTimeout(autoAnalyzeTimer);
      autoAnalyzeTimer = window.setTimeout(maybeTriggerAutoAnalyze, AUTO_ANALYZE_INTERVAL_MS);
    }
  }
}

function maybeTriggerAutoAnalyze() {
  if (!autoAnalyzeEnabled) return;
  if (autoAnalyzeInFlight || chatInFlight) return;
  if (currentRelayState !== "live") return;
  if (!shownSeq || shownSeq === lastAnalyzedSeq) return;

  const now = Date.now();
  const elapsed = now - lastAnalysisTime;
  if (elapsed < AUTO_ANALYZE_INTERVAL_MS) {
    if (!autoAnalyzeTimer) {
      autoAnalyzeTimer = window.setTimeout(() => {
        autoAnalyzeTimer = null;
        maybeTriggerAutoAnalyze();
      }, AUTO_ANALYZE_INTERVAL_MS - elapsed + 100);
    }
    return;
  }

  performAnalysis("auto");
}

if (analyzeBtn) {
  analyzeBtn.addEventListener("click", () => {
    if (autoAnalyzeInFlight) {
      if (actionStatus) actionStatus.textContent = "Analysis already in progress, please wait…";
      return;
    }
    performAnalysis("manual");
  });
}

if (autoAnalyzeToggle) {
  autoAnalyzeToggle.addEventListener("change", () => {
    autoAnalyzeEnabled = autoAnalyzeToggle.checked;
    if (autoAnalyzeEnabled) {
      if (autoScanBadge) {
        autoScanBadge.textContent = "Auto-scan ready";
        autoScanBadge.className = "auto-scan-badge is-idle";
      }
      if (actionStatus) actionStatus.textContent = "Automatic scanning resumed.";
      maybeTriggerAutoAnalyze();
    } else {
      if (autoAnalyzeTimer) window.clearTimeout(autoAnalyzeTimer);
      autoAnalyzeTimer = null;
      if (autoScanBadge) {
        autoScanBadge.textContent = "Auto-scan paused";
        autoScanBadge.className = "auto-scan-badge is-paused";
      }
      if (actionStatus) actionStatus.textContent = "Automatic scanning paused by user.";
    }
  });
}
// --- Shared Inventory & Projects Synchronization ---------------------------

function partMatch(aliases, name) {
  const text = (name || "").toLowerCase().replace(/[-_/]/g, " ");
  return aliases.some((alias) => text.includes(alias.toLowerCase()));
}

async function loadProjects() {
  if (!projectSelect && !stepText) return;
  try {
    const res = await fetch("/api/projects", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    projects = data.projects || [];
    if (projectSelect) {
      projectSelect.replaceChildren();
      projects.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = `Template: ${p.title} (${p.difficulty})`;
        projectSelect.append(opt);
      });
      if (!activeProjectId && projects.length > 0) {
        activeProjectId = projects[0].id;
      }
      projectSelect.value = activeProjectId;
    }
    renderAssistantContext();
  } catch (_err) {}
}

async function loadInventory() {
  if (!quickInvPills && !projectSelect && !stepText) return;
  try {
    const res = await fetch("/api/inventory", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    inventory = data.items || [];
    renderQuickInventory();
    renderAssistantContext();
  } catch (_err) {}
}

function renderQuickInventory() {
  if (!quickInvCount || !quickInvPills) return;
  const confirmed = inventory.filter((i) => i.confirmed);
  const unconfirmed = inventory.filter((i) => !i.confirmed);
  quickInvCount.textContent = `(${confirmed.length} confirmed · ${unconfirmed.length} unconfirmed)`;

  quickInvPills.replaceChildren();
  if (inventory.length === 0) {
    const hint = document.createElement("span");
    hint.className = "empty-hint";
    hint.textContent = "No parts in inventory yet. Capture a frame or confirm items on the workbench.";
    quickInvPills.append(hint);
    return;
  }

  inventory.forEach((item) => {
    const pill = document.createElement("span");
    pill.className = `inv-pill${item.confirmed ? " is-confirmed" : " is-unconfirmed"}`;
    pill.textContent = `${item.confirmed ? "✓ " : "? "}${item.name}`;
    pill.title = `${item.name} (${item.confirmed ? "Confirmed" : "Unconfirmed AI suggestion"})
Evidence: ${item.evidence || "N/A"}`;
    quickInvPills.append(pill);
  });
}

function renderAssistantContext() {
  if (!projectSelect && !chatFeed && !stepText) return;
  const project = projects.find((p) => p.id === activeProjectId) || projects[0];
  if (!project) return;

  const confirmedItems = inventory.filter((i) => i.confirmed && i.name.trim());
  const allItems = inventory.filter((i) => i.name.trim());

  let confirmedCount = 0;
  const missingParts = [];
  const partsStatus = [];

  for (const req of (project.required_parts || [])) {
    const isConfirmed = confirmedItems.some((i) => partMatch(req.aliases, i.name));
    const isUnconfirmed = !isConfirmed && allItems.some((i) => partMatch(req.aliases, i.name));
    let status = "missing";
    if (isConfirmed) {
      status = "confirmed";
      confirmedCount += 1;
    } else if (isUnconfirmed) {
      status = "uncertain";
      missingParts.push(`${req.name} (? unconfirmed)`);
    } else {
      missingParts.push(req.name);
    }
    partsStatus.push({ ...req, status });
  }

  const total = (project.required_parts || []).length;
  if (confirmedItems.length === 0) {
    if (partsConfirmedText) partsConfirmedText.textContent = `Confirmed: 0 / ${total} (No parts confirmed yet)`;
    if (partsMissingText) partsMissingText.textContent = `All ${total} template parts unconfirmed: ${(project.required_parts || []).map(r => r.name).join(", ")}`;
    if (readinessBadge) {
      readinessBadge.className = "readiness-tag readiness-missing";
      readinessBadge.textContent = "No confirmed parts";
    }
  } else {
    if (partsConfirmedText) partsConfirmedText.textContent = `Confirmed: ${confirmedCount} / ${total}`;
    if (partsMissingText) partsMissingText.textContent = missingParts.length ? `Missing: ${missingParts.join(", ")}` : "All parts confirmed!";

    if (readinessBadge) {
      if (confirmedCount === total && total > 0) {
        readinessBadge.className = "readiness-tag readiness-ready";
        readinessBadge.textContent = "Ready to build";
      } else if (confirmedCount >= Math.ceil(total / 2)) {
        readinessBadge.className = "readiness-tag readiness-almost";
        readinessBadge.textContent = `Missing ${total - confirmedCount} part${total - confirmedCount === 1 ? "" : "s"}`;
      } else {
        readinessBadge.className = "readiness-tag readiness-missing";
        readinessBadge.textContent = `Needs ${total - confirmedCount} parts`;
      }
    }
  }

  const steps = project.steps || [];
  if (currentStepIndex > steps.length) currentStepIndex = steps.length || 1;
  if (currentStepIndex < 1) currentStepIndex = 1;

  if (stepTitle) stepTitle.textContent = `Step ${currentStepIndex} of ${steps.length}`;
  if (stepCounter) stepCounter.textContent = `${currentStepIndex} / ${steps.length}`;
  if (stepText) stepText.textContent = steps[currentStepIndex - 1] || "Project completed or overview.";

  const safetyRules = project.safety_checks || [];
  const safetyNote = safetyRules[(currentStepIndex - 1) % safetyRules.length] || "Disconnect all power before wiring.";
  if (stepSafetyText) stepSafetyText.textContent = safetyNote;

  if (prevStepBtn) prevStepBtn.disabled = currentStepIndex <= 1;
  if (nextStepBtn) nextStepBtn.disabled = currentStepIndex >= steps.length;

  // Render Circuit & Pinout Netlist
  renderMonitorCircuit(project, partsStatus, confirmedCount, total);
}

function renderMonitorCircuit(project, partsStatus, confirmedCount, total) {
  if (!monitorCircuitTbody) return;

  const guide = project.circuit_guide;
  if (!guide) {
    if (monitorCircuitTitle) monitorCircuitTitle.textContent = "Illustrative Template";
    if (monitorCircuitStatusTag) {
      monitorCircuitStatusTag.className = "circuit-status-tag tag-incomplete";
      monitorCircuitStatusTag.textContent = "Illustrative Template Only";
    }
    monitorCircuitTbody.innerHTML = '<tr><td colspan="5" class="circuit-empty-note"><strong>Illustrative Template</strong> — No verified circuit definition exists for this template. Do not invent pin mappings.</td></tr>';
    if (monitorTroubleList) {
      monitorTroubleList.innerHTML = '<li class="trouble-item">Disconnect all power before placing or moving parts on the breadboard.</li>';
    }
    return;
  }

  if (monitorCircuitTitle) monitorCircuitTitle.textContent = guide.label || "Circuit & Pinout Netlist";

  const isBuildable = confirmedCount === total && total > 0;
  const missingCount = total - confirmedCount;

  if (monitorCircuitStatusTag) {
    if (isBuildable) {
      monitorCircuitStatusTag.className = "circuit-status-tag tag-ready";
      monitorCircuitStatusTag.textContent = "✓ All Parts Confirmed (Unplugged)";
    } else {
      monitorCircuitStatusTag.className = "circuit-status-tag tag-incomplete";
      monitorCircuitStatusTag.textContent = `Incomplete (${missingCount} unconfirmed) — DO NOT POWER`;
    }
  }

  // Populate connection rows
  monitorCircuitTbody.replaceChildren();
  const connections = guide.connections || [];

  connections.forEach((conn) => {
    const tr = document.createElement("tr");

    // Evaluate connection readiness based on all endpoint parts
    const partIds = conn.required_part_ids || (conn.required_part_id ? [conn.required_part_id] : []);
    const statuses = partIds.map((pid) => {
      const p = partsStatus.find((req) => req.id === pid);
      return p ? p.status : "missing";
    });

    let connStatus = "confirmed";
    if (statuses.length === 0 || statuses.includes("missing")) {
      connStatus = "missing";
    } else if (statuses.includes("uncertain")) {
      connStatus = "uncertain";
    }

    // Net
    const tdNet = document.createElement("td");
    const netPill = document.createElement("span");
    netPill.className = "net-pill";
    netPill.textContent = conn.net;
    tdNet.append(netPill);

    // From
    const tdFrom = document.createElement("td");
    tdFrom.className = "pin-cell";
    const fromComp = document.createElement("strong");
    fromComp.textContent = conn.from_component;
    const fromPin = document.createElement("code");
    fromPin.className = "pin-code";
    fromPin.textContent = conn.from_pin;
    tdFrom.append(fromComp, document.createTextNode(" "), fromPin);

    // To
    const tdTo = document.createElement("td");
    tdTo.className = "pin-cell";
    const toComp = document.createElement("strong");
    toComp.textContent = conn.to_component;
    const toPin = document.createElement("code");
    toPin.className = "pin-code";
    toPin.textContent = conn.to_pin;
    tdTo.append(toComp, document.createTextNode(" "), toPin);

    // Wire
    const tdWire = document.createElement("td");
    const wirePill = document.createElement("span");
    wirePill.className = "wire-pill";
    wirePill.style.borderColor = conn.wire_color;
    const wireDot = document.createElement("span");
    wireDot.className = "wire-dot";
    wireDot.style.background = conn.wire_color;
    wirePill.append(wireDot, document.createTextNode(` ${conn.wire_color_name}`));
    tdWire.append(wirePill);

    // Status
    const tdStatus = document.createElement("td");
    const statusPill = document.createElement("span");
    if (connStatus === "confirmed") {
      statusPill.className = "status-pill is-confirmed";
      statusPill.textContent = "✓ Confirmed";
    } else if (connStatus === "uncertain") {
      statusPill.className = "status-pill is-uncertain";
      statusPill.textContent = "? Unconfirmed";
    } else {
      statusPill.className = "status-pill is-missing";
      statusPill.textContent = "✗ Missing";
    }
    tdStatus.append(statusPill);

    tr.append(tdNet, tdFrom, tdTo, tdWire, tdStatus);
    monitorCircuitTbody.append(tr);
  });

  // Populate troubleshooting
  if (monitorTroubleList) {
    monitorTroubleList.replaceChildren();
    (guide.troubleshooting || []).forEach((tText, idx) => {
      const li = document.createElement("li");
      li.className = "trouble-item";
      const num = document.createElement("strong");
      num.textContent = `${idx + 1}. `;
      li.append(num, document.createTextNode(tText));
      monitorTroubleList.append(li);
    });
  }
}

// --- Right Panel: Lab Assistant Chat Logic ----------------------------------

function appendChatMessage(sender, text, isError = false) {
  if (!chatFeed) return;
  const msg = document.createElement("div");
  msg.className = `chat-msg ${sender}${isError ? " is-error" : ""}`;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";

  const header = document.createElement("div");
  header.className = "bubble-header";
  const name = document.createElement("strong");
  name.textContent = sender === "user" ? "You" : "BoardBuddy Lab Copilot";
  const time = document.createElement("span");
  time.className = "bubble-time";
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  header.append(name, time);
  bubble.append(header);

  const lines = text.split("\n");
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const p = document.createElement("p");
    p.textContent = trimmed;
    bubble.append(p);
  });

  msg.append(bubble);
  chatFeed.append(msg);
  chatFeed.scrollTop = chatFeed.scrollHeight;
}

async function sendChatMessage(message) {
  const text = (message || "").trim();
  if (!text) return;
  if (chatInFlight) return; // Prevent overlapping chat submissions

  chatInFlight = true;
  appendChatMessage("user", text);
  if (chatInput) chatInput.value = "";
  if (chatSendBtn) chatSendBtn.disabled = true;

  // Pause auto-scan during active chat session so CPU is 100% dedicated to answering
  if (autoAnalyzeTimer) {
    window.clearTimeout(autoAnalyzeTimer);
    autoAnalyzeTimer = null;
  }
  if (autoScanBadge && !autoAnalyzeInFlight) {
    autoScanBadge.textContent = "Paused for chat";
    autoScanBadge.className = "auto-scan-badge is-paused";
  }

  const thinkingMsg = document.createElement("div");
  thinkingMsg.className = "chat-msg assistant is-thinking";
  thinkingMsg.innerHTML = '<div class="chat-bubble"><p><em>Gemma is analyzing your question and bench context…</em></p></div>';
  if (chatFeed) {
    chatFeed.append(thinkingMsg);
    chatFeed.scrollTop = chatFeed.scrollHeight;
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        message: text,
        project_id: activeProjectId,
        current_step: currentStepIndex,
      }),
    });
    window.clearTimeout(timeoutId);
    thinkingMsg.remove();
    const data = await response.json();
    if (!response.ok) {
      appendChatMessage("assistant", `⚠️ ${data.error || "Could not get an answer from the local model."}`, true);
    } else {
      appendChatMessage("assistant", data.reply || "No reply generated.");
    }
  } catch (err) {
    window.clearTimeout(timeoutId);
    thinkingMsg.remove();
    if (err.name === "AbortError") {
      appendChatMessage("assistant", "⚠️ Request timed out after 20s. Local CPU is busy; please try asking a shorter question or retry.", true);
    } else {
      appendChatMessage("assistant", `⚠️ Connection error: ${err.message}. Check that Ollama is running.`, true);
    }
  } finally {
    chatInFlight = false;
    if (chatSendBtn) chatSendBtn.disabled = false;
    if (chatInput) chatInput.focus();

    // Safely resume auto-scan after chat finishes
    if (autoAnalyzeEnabled && currentRelayState === "live" && !autoAnalyzeInFlight) {
      if (autoScanBadge) {
        autoScanBadge.textContent = "Auto-scan ready";
        autoScanBadge.className = "auto-scan-badge is-idle";
      }
      if (autoAnalyzeTimer) window.clearTimeout(autoAnalyzeTimer);
      autoAnalyzeTimer = window.setTimeout(maybeTriggerAutoAnalyze, 2000); // 2s pause before resuming
    }
  }
}

// --- Initialize Assistant Panel (Guarded) -----------------------------------

function initAssistantPanel() {
  if (!projectSelect && !chatFeed && !quickInvPills) {
    return; // Assistant panel elements not present; skip safely
  }

  // Build Guide Tab Switcher
  function switchTab(activeTab) {
    const tabs = [
      { btn: tabBtnSteps, panel: tabPanelSteps, id: "steps" },
      { btn: tabBtnCircuit, panel: tabPanelCircuit, id: "circuit" },
      { btn: tabBtnTrouble, panel: tabPanelTrouble, id: "trouble" }
    ];
    tabs.forEach((t) => {
      if (!t.btn || !t.panel) return;
      const isActive = t.id === activeTab;
      t.btn.classList.toggle("is-active", isActive);
      t.btn.setAttribute("aria-selected", isActive ? "true" : "false");
      t.panel.hidden = !isActive;
    });
  }

  if (tabBtnSteps) tabBtnSteps.addEventListener("click", () => switchTab("steps"));
  if (tabBtnCircuit) tabBtnCircuit.addEventListener("click", () => switchTab("circuit"));
  if (tabBtnTrouble) tabBtnTrouble.addEventListener("click", () => switchTab("trouble"));

  if (projectSelect) {
    projectSelect.addEventListener("change", () => {
      activeProjectId = projectSelect.value;
      currentStepIndex = 1;
      renderAssistantContext();
    });
  }

  if (prevStepBtn) {
    prevStepBtn.addEventListener("click", () => {
      if (currentStepIndex > 1) {
        currentStepIndex -= 1;
        renderAssistantContext();
      }
    });
  }

  if (nextStepBtn) {
    nextStepBtn.addEventListener("click", () => {
      const project = projects.find((p) => p.id === activeProjectId);
      if (project && currentStepIndex < (project.steps || []).length) {
        currentStepIndex += 1;
        renderAssistantContext();
      }
    });
  }

  if (chatForm) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (chatInput) sendChatMessage(chatInput.value);
    });
  }

  document.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const prompt = chip.getAttribute("data-prompt");
      if (prompt) sendChatMessage(prompt);
    });
  });

  loadProjects().then(loadInventory);
  setInterval(loadInventory, 4000);
}

initAssistantPanel();
