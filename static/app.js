
async function scaleImageFile(file, maxEdge = 800, quality = 0.75) {
  return new Promise((resolve) => {
    if (!file || !file.type || !file.type.startsWith("image/")) {
      resolve(file);
      return;
    }
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const width = img.naturalWidth || img.width;
      const height = img.naturalHeight || img.height;
      if (Math.max(width, height) <= maxEdge) {
        resolve(file);
        return;
      }
      const scale = maxEdge / Math.max(width, height);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        resolve(file);
        return;
      }
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        if (!blob) {
          resolve(file);
          return;
        }
        resolve(new File([blob], (file.name || "photo").replace(/\.[^/.]+$/, "") + ".jpg", { type: "image/jpeg" }));
      }, "image/jpeg", quality);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(file);
    };
    img.src = url;
  });
}

const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const fileInput = document.querySelector("#image-input");
const uploadZone = document.querySelector("#upload-zone");
const previewWrap = document.querySelector("#preview-wrap");
const previewImage = document.querySelector("#image-preview");
const fileName = document.querySelector("#file-name");
const analyzeButton = document.querySelector("#analyze-button");
const uploadMessage = document.querySelector("#upload-message");
const serviceStatus = document.querySelector("#service-status");
const serviceStatusText = document.querySelector("#service-status-text");

const inventoryCount = document.querySelector("#inventory-count");
const suggestionsList = document.querySelector("#suggestions-list");
const clearInventoryBtn = document.querySelector("#clear-inventory");
const projectsGrid = document.querySelector("#projects-grid");

const activeTitle = document.querySelector("#active-project-title");
const activeSummary = document.querySelector("#active-project-summary");
const activeDifficulty = document.querySelector("#active-project-difficulty");
const activeOptional = document.querySelector("#active-project-optional");
const partsList = document.querySelector("#parts-list");
const safetyChecklist = document.querySelector("#safety-checklist");
const projectSteps = document.querySelector("#project-steps");
const progressLabel = document.querySelector("#step-progress-label");
const progressFill = document.querySelector("#progress-bar-fill");
const circuitGuideCard = document.querySelector("#circuit-guide-card");
const circuitGuideTitle = document.querySelector("#circuit-guide-title");
const circuitGuideSubtitle = document.querySelector("#circuit-guide-subtitle");
const circuitTemplateBadge = document.querySelector("#circuit-template-badge");
const circuitStatusBadge = document.querySelector("#circuit-status-badge");
const circuitPowerText = document.querySelector("#circuit-power-source-text");
const circuitCanvasContainer = document.querySelector("#circuit-canvas-container");
const circuitConnectionsTbody = document.querySelector("#circuit-connections-tbody");
const troubleshootingList = document.querySelector("#troubleshooting-list");

let selectedFile = null;
let previewUrl = null;
let inventory = [];
let projects = [];
let activeProjectId = null;
let completedSteps = new Set();
let checkedSafety = new Set();

function setMessage(message) {
  uploadMessage.textContent = message;
}

function setSelectedFile(file) {
  setMessage("");
  if (!file) return;
  if (!allowedTypes.has(file.type)) {
    setMessage("Choose a JPEG, PNG, or WebP image.");
    return;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    setMessage("That image is over the 8 MiB limit. Choose a smaller photo.");
    return;
  }

  selectedFile = file;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  previewImage.src = previewUrl;
  fileName.textContent = file.name;
  uploadZone.hidden = true;
  previewWrap.hidden = false;
  analyzeButton.disabled = false;
}

function setServiceStatus(state, text) {
  serviceStatus.dataset.state = state;
  serviceStatusText.textContent = text;
}

async function checkService() {
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    if (health.ollama !== "online") {
      setServiceStatus("offline", "Ollama is not responding locally");
    } else if (!health.model_available) {
      setServiceStatus("missing", `${health.model} is not installed`);
    } else {
      setServiceStatus("online", `Local · ${health.model} ready`);
    }
  } catch (_error) {
    setServiceStatus("offline", "Could not check local Ollama");
  }
}

function addText(parent, tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  parent.append(element);
  return element;
}

// --- Shared Inventory API ---------------------------------------------------

async function loadInventory() {
  try {
    const response = await fetch("/api/inventory", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    inventory = data.items || [];
    renderInventory();
    renderProjects();
    renderActiveProject();
  } catch (_err) {
    // transient network error; will retry on next poll
  }
}

async function updateInventoryItem(id, updates) {
  try {
    const response = await fetch(`/api/inventory/${id}`, {
      method: "PATCH",
      headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!response.ok) return;
    const data = await response.json();
    const idx = inventory.findIndex((item) => item.id === id);
    if (idx !== -1) {
      inventory[idx] = data.item;
    }
    renderInventory();
    renderProjects();
    renderActiveProject();
  } catch (_err) {
    setMessage("Could not update component on server.");
  }
}

async function deleteInventoryItem(id) {
  try {
    const response = await fetch(`/api/inventory/${id}`, {
      method: "DELETE",
      headers: { "X-CSRF-Token": csrfToken },
    });
    if (!response.ok) return;
    inventory = inventory.filter((item) => item.id !== id);
    renderInventory();
    renderProjects();
    renderActiveProject();
  } catch (_err) {
    setMessage("Could not delete component.");
  }
}

async function addManualPart() {
  try {
    const response = await fetch("/api/inventory", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "New component",
        evidence: "Added manually on workbench.",
        uncertainty: "Verify markings and pinout from datasheet.",
        confirmed: false,
        source: "manual",
      }),
    });
    if (!response.ok) return;
    const data = await response.json();
    inventory = data.items || [];
    renderInventory();
    renderProjects();
    renderActiveProject();
    const firstInput = suggestionsList.querySelector(".component-name");
    if (firstInput) firstInput.focus();
  } catch (_err) {
    setMessage("Could not add part.");
  }
}

async function clearAllInventory() {
  if (!confirm("Clear all items from your bench inventory?")) return;
  try {
    await fetch("/api/inventory/clear", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
    });
    inventory = [];
    renderInventory();
    renderProjects();
    renderActiveProject();
  } catch (_err) {
    setMessage("Could not clear inventory.");
  }
}

function renderInventory() {
  inventoryCount.textContent = `(${inventory.length})`;
  suggestionsList.replaceChildren();
  if (inventory.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const mark = addText(empty, "span", "empty-mark", "📦");
    mark.setAttribute("aria-hidden", "true");
    addText(empty, "p", "", "No components in inventory yet.");
    addText(empty, "span", "", "Capture a photo here, or use the phone camera to send parts to this bench.");
    suggestionsList.append(empty);
    return;
  }

  inventory.forEach((part) => {
    const card = document.createElement("article");
    card.className = `component-card${part.confirmed ? " is-confirmed" : ""}`;

    const head = document.createElement("div");
    head.className = "component-head";

    const nameInput = document.createElement("input");
    nameInput.className = "component-name";
    nameInput.type = "text";
    nameInput.maxLength = 120;
    nameInput.value = part.name;
    nameInput.setAttribute("aria-label", "Component name; edit to correct");
    nameInput.addEventListener("change", () => {
      updateInventoryItem(part.id, { name: nameInput.value });
    });
    head.append(nameInput);

    if (part.source) {
      const srcBadge = addText(head, "span", "source-badge", part.source);
      srcBadge.title = `Identified via ${part.source}`;
    }

    const confirmLabel = document.createElement("label");
    confirmLabel.className = "confirm-label";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = part.confirmed;
    checkbox.setAttribute("aria-label", `Confirm ${part.name || "this part"}`);
    checkbox.addEventListener("change", () => {
      updateInventoryItem(part.id, { confirmed: checkbox.checked });
    });
    confirmLabel.append(checkbox, document.createTextNode("I confirm"));
    head.append(confirmLabel);

    const removeButton = document.createElement("button");
    removeButton.className = "remove-part";
    removeButton.type = "button";
    removeButton.textContent = "✕";
    removeButton.setAttribute("aria-label", `Remove ${part.name || "this part"}`);
    removeButton.addEventListener("click", () => deleteInventoryItem(part.id));
    head.append(removeButton);

    card.append(head);
    addText(card, "p", "component-detail", `Evidence: ${part.evidence || "None listed."}`);
    addText(card, "p", "component-detail", `Uncertainty: ${part.uncertainty || "Verify pinout."}`);
    suggestionsList.append(card);
  });
}

// --- Project Catalog & Recommendation Engine --------------------------------

function partMatch(requiredAliases, itemName) {
  const text = (itemName || "").toLowerCase().replace(/[\-_/]/g, " ");
  return requiredAliases.some((alias) => text.includes(alias.toLowerCase()));
}

function evaluateProject(project) {
  const confirmedItems = inventory.filter((item) => item.confirmed && item.name.trim());
  const allItems = inventory.filter((item) => item.name.trim());

  if (confirmedItems.length === 0) {
    const total = project.required_parts.length;
    return {
      readiness: "missing",
      badgeText: "No parts confirmed",
      confirmedCount: 0,
      total,
      partsStatus: project.required_parts.map((req) => {
        const hasUnconfirmed = allItems.some((item) => partMatch(req.aliases, item.name));
        return { ...req, status: hasUnconfirmed ? "uncertain" : "missing" };
      }),
    };
  }

  let confirmedCount = 0;
  const partsStatus = [];

  for (const req of project.required_parts) {
    const hasConfirmed = confirmedItems.some((item) => partMatch(req.aliases, item.name));
    const hasUnconfirmed = !hasConfirmed && allItems.some((item) => partMatch(req.aliases, item.name));

    let status = "missing";
    if (hasConfirmed) {
      status = "confirmed";
      confirmedCount += 1;
    } else if (hasUnconfirmed) {
      status = "uncertain";
    }

    partsStatus.push({ ...req, status });
  }

  const total = project.required_parts.length;
  let readiness = "missing";
  let badgeText = `Needs ${total - confirmedCount} parts`;

  if (confirmedCount === total) {
    readiness = "ready";
    badgeText = "Ready to build";
  } else if (confirmedCount >= Math.ceil(total / 2)) {
    readiness = "almost";
    badgeText = `Missing ${total - confirmedCount} part${total - confirmedCount === 1 ? "" : "s"}`;
  }

  return { readiness, badgeText, confirmedCount, total, partsStatus };
}

async function loadProjects() {
  try {
    const response = await fetch("/api/projects", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    projects = data.projects || [];
    if (!activeProjectId && projects.length > 0) {
      activeProjectId = projects[0].id;
    }
    renderProjects();
    renderActiveProject();
  } catch (_err) {
    // transient error
  }
}

function renderProjects() {
  projectsGrid.replaceChildren();
  if (projects.length === 0) return;

  projects.forEach((proj) => {
    const evaluation = evaluateProject(proj);
    const card = document.createElement("article");
    const isSelected = proj.id === activeProjectId;
    card.className = `project-card${isSelected ? " is-selected" : ""}`;
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-pressed", isSelected ? "true" : "false");

    const header = document.createElement("div");
    header.className = "project-card-header";
    addText(header, "span", "project-card-diff", proj.difficulty);
    addText(header, "span", `readiness-tag readiness-${evaluation.readiness}`, evaluation.badgeText);
    card.append(header);

    addText(card, "h3", "project-card-title", proj.title);
    addText(card, "p", "project-card-summary", proj.summary);

    const stats = document.createElement("div");
    stats.className = "project-card-stats";
    addText(stats, "span", "stats-ratio", `Confirmed: ${evaluation.confirmedCount} / ${evaluation.total} parts`);

    const selectBtn = document.createElement("button");
    selectBtn.type = "button";
    selectBtn.className = isSelected ? "project-select-btn is-active" : "project-select-btn";
    selectBtn.textContent = isSelected ? "Active Guide ✓" : "Select Project →";
    stats.append(selectBtn);
    card.append(stats);

    const activate = () => {
      activeProjectId = proj.id;
      completedSteps.clear();
      checkedSafety.clear();
      renderProjects();
      renderActiveProject();
      document.querySelector("#active-project-section").scrollIntoView({ behavior: "smooth" });
    };

    card.addEventListener("click", activate);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activate();
      }
    });

    projectsGrid.append(card);
  });
}

function renderActiveProject() {
  const current = projects.find((p) => p.id === activeProjectId) || projects[0];
  if (!current) return;

  activeTitle.textContent = current.title;
  activeSummary.textContent = current.summary;
  activeDifficulty.textContent = current.difficulty;

  if (current.optional_note) {
    activeOptional.textContent = `Note: ${current.optional_note}`;
    activeOptional.hidden = false;
  } else {
    activeOptional.hidden = true;
  }

  // 1. Render Checklist
  const evaluation = evaluateProject(current);
  partsList.replaceChildren();
  const labels = { confirmed: "Confirmed", uncertain: "Uncertain", missing: "Missing" };

  for (const part of evaluation.partsStatus) {
    const row = document.createElement("li");
    row.className = "part-row";

    const nameGroup = document.createElement("span");
    nameGroup.className = "part-name";
    nameGroup.append(document.createTextNode(part.name));
    addText(nameGroup, "span", "part-quantity", ` · ${part.quantity}`);

    const statusNode = addText(row, "span", `part-status status-${part.status}`, labels[part.status]);
    statusNode.setAttribute("aria-label", `${part.name}: ${labels[part.status]}`);
    row.prepend(nameGroup);
    partsList.append(row);
  }

  // 2. Render Safety Checks
  safetyChecklist.replaceChildren();
  (current.safety_checks || []).forEach((check, index) => {
    const item = document.createElement("li");
    item.className = "safety-item";

    const label = document.createElement("label");
    label.className = "safety-label";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = checkedSafety.has(index);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) checkedSafety.add(index);
      else checkedSafety.delete(index);
    });

    label.append(checkbox, document.createTextNode(check));
    item.append(label);
    safetyChecklist.append(item);
  });

  // 3. Render Steps with progress tracking
  projectSteps.replaceChildren();
  const steps = current.steps || [];
  progressLabel.textContent = `${completedSteps.size} of ${steps.length} completed`;
  const pct = steps.length ? Math.round((completedSteps.size / steps.length) * 100) : 0;
  progressFill.style.width = `${pct}%`;

  steps.forEach((stepText, index) => {
    const li = document.createElement("li");
    li.className = `step-item${completedSteps.has(index) ? " is-completed" : ""}`;

    const label = document.createElement("label");
    label.className = "step-label-wrap";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "step-checkbox";
    checkbox.checked = completedSteps.has(index);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) completedSteps.add(index);
      else completedSteps.delete(index);
      renderActiveProject();
    });

    const num = addText(label, "span", "step-num", `${index + 1}.`);
    const textNode = document.createElement("span");
    textNode.className = "step-text";
    textNode.textContent = stepText;

    label.prepend(checkbox);
    label.append(textNode);
    li.append(label);
    projectSteps.append(li);
  });

  // 4. Render 2D Circuit Guide & Netlist
  renderCircuitGuide(current, evaluation);
}

// --- Upload Photo & Analyze -------------------------------------------------



function renderCircuitGuide(current, evaluation) {
  if (!circuitGuideCard) return;

  const guide = current.circuit_guide;
  if (!guide) {
    if (circuitTemplateBadge) {
      circuitTemplateBadge.textContent = "Illustrative Template Only";
      circuitTemplateBadge.className = "circuit-template-badge is-illustrative";
    }
    if (circuitStatusBadge) {
      circuitStatusBadge.textContent = "No verified circuit definition";
      circuitStatusBadge.className = "circuit-status-badge status-missing";
    }
    if (circuitPowerText) {
      circuitPowerText.textContent = "No supported hardware circuit definition exists for this template. Do not invent pin mappings. Always disconnect power before wiring.";
    }
    if (circuitCanvasContainer) {
      circuitCanvasContainer.innerHTML = '<div class="diagram-empty-notice"><strong>Illustrative Template Only:</strong> No verified hardware circuit definition exists for this template. Do not invent pin mappings.</div>';
    }
    if (circuitConnectionsTbody) {
      circuitConnectionsTbody.innerHTML = '<tr><td colspan="6" class="circuit-empty-note">No verified wiring netlist for this template. Do not invent pin mappings.</td></tr>';
    }
    if (troubleshootingList) {
      troubleshootingList.innerHTML = '<li class="trouble-item"><span class="trouble-badge-item">Safety</span> <span class="trouble-text">Disconnect all power before placing or moving parts on the breadboard.</span></li>';
    }
    return;
  }

  // Populate verified template headers
  if (circuitGuideTitle) circuitGuideTitle.textContent = `2D Circuit Guide: ${guide.label || current.title}`;
  if (circuitGuideSubtitle) circuitGuideSubtitle.textContent = guide.breadboard_layout_summary || "Component-to-pin wiring netlist with confirmed-parts check";

  if (circuitTemplateBadge) {
    circuitTemplateBadge.textContent = guide.template_type || "Verified Reference Circuit";
    circuitTemplateBadge.className = "circuit-template-badge is-verified";
  }

  const isBuildable = evaluation.confirmedCount === evaluation.total && evaluation.total > 0;
  const missingCount = evaluation.total - evaluation.confirmedCount;

  if (circuitStatusBadge) {
    if (isBuildable) {
      circuitStatusBadge.textContent = "✓ All Parts Confirmed · Ready to Wire (USB Unplugged)";
      circuitStatusBadge.className = "circuit-status-badge status-ready";
    } else {
      circuitStatusBadge.textContent = `Incomplete (${missingCount} part${missingCount === 1 ? "" : "s"} unconfirmed) — DO NOT POWER`;
      circuitStatusBadge.className = "circuit-status-badge status-missing";
    }
  }

  if (circuitPowerText) {
    circuitPowerText.textContent = `${guide.power_source}. Always keep USB unplugged while inserting or moving jumper wires. Never connect unknown voltages or power an unverified circuit.`;
  }

  // 1. Render Visual 2D Schematic Canvas
  if (circuitCanvasContainer) {
    circuitCanvasContainer.innerHTML = generateCircuitSvg(current.id, guide, evaluation);
  }

  // 2. Render Connection Table Netlist
  if (circuitConnectionsTbody) {
    circuitConnectionsTbody.replaceChildren();
    const connections = guide.connections || [];

    connections.forEach((conn) => {
      const tr = document.createElement("tr");

      // Evaluate connection readiness based on all endpoint parts
      const partIds = conn.required_part_ids || (conn.required_part_id ? [conn.required_part_id] : []);
      const statuses = partIds.map((pid) => {
        const p = evaluation.partsStatus.find((req) => req.id === pid);
        return p ? p.status : "missing";
      });

      let partStatus = "confirmed";
      if (statuses.length === 0 || statuses.includes("missing")) {
        partStatus = "missing";
      } else if (statuses.includes("uncertain")) {
        partStatus = "uncertain";
      }

      // Net
      const tdNet = document.createElement("td");
      const netPill = document.createElement("span");
      netPill.className = "net-pill";
      netPill.textContent = conn.net;
      tdNet.append(netPill);

      // From Pin
      const tdFrom = document.createElement("td");
      tdFrom.className = "pin-cell";
      const fromStrong = document.createElement("strong");
      fromStrong.textContent = conn.from_component;
      const fromCode = document.createElement("code");
      fromCode.className = "pin-badge";
      fromCode.textContent = conn.from_pin;
      tdFrom.append(fromStrong, document.createTextNode(" "), fromCode);

      // To Pin
      const tdTo = document.createElement("td");
      tdTo.className = "pin-cell";
      const toStrong = document.createElement("strong");
      toStrong.textContent = conn.to_component;
      const toCode = document.createElement("code");
      toCode.className = "pin-badge";
      toCode.textContent = conn.to_pin;
      tdTo.append(toStrong, document.createTextNode(" "), toCode);

      // Wire Color Pill
      const tdWire = document.createElement("td");
      const wirePill = document.createElement("span");
      wirePill.className = "wire-pill";
      wirePill.style.borderColor = conn.wire_color;
      const wireDot = document.createElement("span");
      wireDot.className = "wire-dot";
      wireDot.style.background = conn.wire_color;
      wirePill.append(wireDot, document.createTextNode(` ${conn.wire_color_name}`));
      tdWire.append(wirePill);

      // Part Status
      const tdStatus = document.createElement("td");
      const statusPill = document.createElement("span");
      if (partStatus === "confirmed") {
        statusPill.className = "part-status status-confirmed";
        statusPill.textContent = "✓ Confirmed";
      } else if (partStatus === "uncertain") {
        statusPill.className = "part-status status-uncertain";
        statusPill.textContent = "? Unconfirmed";
      } else {
        statusPill.className = "part-status status-missing";
        statusPill.textContent = "✗ Missing";
      }
      tdStatus.append(statusPill);

      // Notes
      const tdNotes = document.createElement("td");
      tdNotes.className = "conn-notes";
      tdNotes.textContent = conn.notes || "—";

      tr.append(tdNet, tdFrom, tdTo, tdWire, tdStatus, tdNotes);
      circuitConnectionsTbody.append(tr);
    });
  }

  // 3. Render Troubleshooting Checklist
  if (troubleshootingList) {
    troubleshootingList.replaceChildren();
    const items = guide.troubleshooting || [];
    items.forEach((itemText) => {
      const li = document.createElement("li");
      li.className = "trouble-item";

      const badge = document.createElement("span");
      badge.className = "trouble-badge-item";
      badge.textContent = "Check";

      const text = document.createElement("span");
      text.className = "trouble-text";
      text.textContent = itemText;

      li.append(badge, text);
      troubleshootingList.append(li);
    });
  }
}

// --- 2D Circuit Diagram SVG Generator (AR-Assemply inspired) ----------------

function generateCircuitSvg(projectId, guide, evaluation) {
  const isBuildable = evaluation.confirmedCount === evaluation.total;
  const statusWatermark = isBuildable
    ? `<rect x="420" y="8" width="245" height="26" rx="4" fill="#065f46" />
       <text x="542" y="25" fill="#ecfdf5" font-size="11" font-weight="bold" text-anchor="middle">✓ CONFIRMED · UNPLUG USB TO WIRE</text>`
    : `<rect x="420" y="8" width="245" height="26" rx="4" fill="#991b1b" />
       <text x="542" y="25" fill="#fef2f2" font-size="11" font-weight="bold" text-anchor="middle">⚠️ UNCONFIRMED · DO NOT POWER</text>`;

  if (projectId === "esp32-temp") {
    return `
      <svg viewBox="0 0 680 200" class="circuit-svg" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <filter id="wire-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="1" stdDeviation="1" flood-opacity="0.3" />
          </filter>
        </defs>
        <!-- Background Grid -->
        <rect width="680" height="200" fill="#0f172a" rx="8" />
        <pattern id="dot-grid" width="16" height="16" patternUnits="userSpaceOnUse">
          <circle cx="8" cy="8" r="0.8" fill="#334155" />
        </pattern>
        <rect width="680" height="200" fill="url(#dot-grid)" rx="8" />

        <!-- Status Watermark -->
        ${statusWatermark}

        <!-- Breadboard Rails Indicator -->
        <line x1="20" y1="185" x2="660" y2="185" stroke="#ef4444" stroke-width="2" stroke-dasharray="4 4" opacity="0.6" />
        <text x="24" y="180" fill="#ef4444" font-size="10" font-weight="bold">+ 3.3V POWER RAIL (UNPLUGGED)</text>
        <line x1="20" y1="195" x2="660" y2="195" stroke="#38bdf8" stroke-width="2" stroke-dasharray="4 4" opacity="0.6" />
        <text x="24" y="193" fill="#38bdf8" font-size="9" font-weight="bold">- COMMON GROUND RAIL (0V)</text>

        <!-- ESP32 DevKit Module Block -->
        <rect x="30" y="35" width="160" height="130" rx="6" fill="#1e293b" stroke="#475569" stroke-width="2" />
        <rect x="30" y="35" width="160" height="24" rx="6" fill="#334155" />
        <text x="110" y="51" fill="#f8fafc" font-size="12" font-weight="bold" text-anchor="middle">ESP32 DevKit</text>
        <text x="110" y="65" fill="#94a3b8" font-size="9" text-anchor="middle">38-pin Low Voltage</text>

        <!-- ESP32 Pin Pads -->
        <circle cx="175" cy="85" r="5" fill="#ef4444" />
        <text x="165" y="89" fill="#f8fafc" font-size="11" text-anchor="end" font-family="monospace">3V3</text>

        <circle cx="175" cy="115" r="5" fill="#38bdf8" />
        <text x="165" y="119" fill="#f8fafc" font-size="11" text-anchor="end" font-family="monospace">GND</text>

        <circle cx="175" cy="145" r="5" fill="#f59e0b" />
        <text x="165" y="149" fill="#f8fafc" font-size="11" text-anchor="end" font-family="monospace">GPIO 4</text>

        <!-- 10k Pull-up Resistor Box -->
        <rect x="290" y="70" width="85" height="42" rx="4" fill="#1e293b" stroke="#eab308" stroke-width="1.5" />
        <text x="332" y="87" fill="#fef08a" font-size="10" font-weight="bold" text-anchor="middle">10 kΩ Pull-up</text>
        <text x="332" y="102" fill="#94a3b8" font-size="8" text-anchor="middle">(DATA &lt;&gt; 3V3)</text>
        <circle cx="290" cy="91" r="4" fill="#ef4444" />
        <circle cx="375" cy="91" r="4" fill="#3b82f6" />

        <!-- DHT22 Sensor Module Block -->
        <rect x="470" y="35" width="170" height="130" rx="6" fill="#1e293b" stroke="#38bdf8" stroke-width="2" />
        <rect x="470" y="35" width="170" height="24" rx="6" fill="#0369a1" />
        <text x="555" y="51" fill="#f8fafc" font-size="12" font-weight="bold" text-anchor="middle">DHT22 / AM2302</text>
        <text x="555" y="65" fill="#bae6fd" font-size="9" text-anchor="middle">Temp &amp; Humidity Sensor</text>

        <!-- DHT22 Pin Pads -->
        <circle cx="485" cy="85" r="5" fill="#ef4444" />
        <text x="496" y="89" fill="#f8fafc" font-size="10" font-family="monospace">1: VCC (3.3V)</text>

        <circle cx="485" cy="110" r="5" fill="#f59e0b" />
        <text x="496" y="114" fill="#f8fafc" font-size="10" font-family="monospace">2: DATA (Sig)</text>

        <circle cx="485" cy="130" r="4" fill="#64748b" opacity="0.6" />
        <text x="496" y="133" fill="#64748b" font-size="9" font-family="monospace">3: NC (Do not connect)</text>

        <circle cx="485" cy="150" r="5" fill="#38bdf8" />
        <text x="496" y="154" fill="#f8fafc" font-size="10" font-family="monospace">4: GND (0V)</text>

        <!-- Color-Coded Routed Wires -->
        <!-- Wire 1: 3.3V (Red) -->
        <path d="M 175 85 C 230 85, 230 45, 290 91" stroke="#ef4444" stroke-width="3" fill="none" filter="url(#wire-glow)" />
        <path d="M 175 85 C 250 85, 410 85, 485 85" stroke="#ef4444" stroke-width="3" fill="none" filter="url(#wire-glow)" />
        <text x="240" y="78" fill="#fca5a5" font-size="9" font-weight="bold">3.3V VCC (Red)</text>

        <!-- Wire 2: GND (Cyan/Black) -->
        <path d="M 175 115 C 260 115, 390 150, 485 150" stroke="#38bdf8" stroke-width="3" fill="none" filter="url(#wire-glow)" />
        <text x="240" y="132" fill="#7dd3fc" font-size="9" font-weight="bold">GND 0V (Black)</text>

        <!-- Wire 3: DATA Signal (Yellow) -->
        <path d="M 175 145 C 260 145, 400 110, 485 110" stroke="#f59e0b" stroke-width="3" fill="none" filter="url(#wire-glow)" />
        <text x="240" y="152" fill="#fde047" font-size="9" font-weight="bold">GPIO4 &lt;&gt; DATA (Yellow)</text>

        <!-- Wire 4: Resistor pull-up to DATA (Blue) -->
        <path d="M 375 91 C 420 91, 440 110, 485 110" stroke="#3b82f6" stroke-width="2.5" fill="none" stroke-dasharray="3 3" />
      </svg>
    `;
  }

  if (projectId === "led-blinker") {
    return `
      <svg viewBox="0 0 680 200" class="circuit-svg" xmlns="http://www.w3.org/2000/svg">
        <rect width="680" height="200" fill="#0f172a" rx="8" />
        ${statusWatermark}
        <!-- ESP32 Module -->
        <rect x="30" y="35" width="150" height="130" rx="6" fill="#1e293b" stroke="#475569" stroke-width="2" />
        <rect x="30" y="35" width="150" height="24" rx="6" fill="#334155" />
        <text x="105" y="51" fill="#f8fafc" font-size="12" font-weight="bold" text-anchor="middle">ESP32 DevKit</text>
        <circle cx="165" cy="85" r="5" fill="#22c55e" />
        <text x="155" y="89" fill="#f8fafc" font-size="11" text-anchor="end" font-family="monospace">GPIO 2</text>
        <circle cx="165" cy="135" r="5" fill="#38bdf8" />
        <text x="155" y="139" fill="#f8fafc" font-size="11" text-anchor="end" font-family="monospace">GND</text>

        <!-- LED Component -->
        <rect x="280" y="55" width="100" height="90" rx="6" fill="#1e293b" stroke="#22c55e" stroke-width="1.5" />
        <text x="330" y="74" fill="#86efac" font-size="11" font-weight="bold" text-anchor="middle">LED (Diode)</text>
        <circle cx="295" cy="100" r="5" fill="#22c55e" />
        <text x="305" y="104" fill="#f8fafc" font-size="10">Anode (+)</text>
        <circle cx="365" cy="100" r="5" fill="#94a3b8" />
        <text x="355" y="125" fill="#94a3b8" font-size="9" text-anchor="end">Cathode (-)</text>

        <!-- Current-limiting Resistor -->
        <rect x="440" y="80" width="100" height="42" rx="4" fill="#1e293b" stroke="#eab308" stroke-width="1.5" />
        <text x="490" y="98" fill="#fde047" font-size="10" font-weight="bold" text-anchor="middle">220Ω - 330Ω</text>
        <text x="490" y="112" fill="#94a3b8" font-size="8" text-anchor="middle">Current Limiter</text>
        <circle cx="440" cy="101" r="4" fill="#94a3b8" />
        <circle cx="540" cy="101" r="5" fill="#38bdf8" />

        <!-- Wires -->
        <path d="M 165 85 C 220 85, 240 100, 295 100" stroke="#22c55e" stroke-width="3" fill="none" />
        <text x="210" y="80" fill="#86efac" font-size="9" font-weight="bold">GPIO 2 (Green)</text>
        <line x1="365" y1="100" x2="440" y2="101" stroke="#94a3b8" stroke-width="3" />
        <text x="385" y="93" fill="#cbd5e1" font-size="8">Series</text>
        <path d="M 540 101 C 600 101, 600 170, 350 170 C 220 170, 165 170, 165 135" stroke="#38bdf8" stroke-width="3" fill="none" />
        <text x="440" y="165" fill="#7dd3fc" font-size="9" font-weight="bold">Return to GND (Black)</text>
      </svg>
    `;
  }

  if (projectId === "ldr-sensor") {
    return `
      <svg viewBox="0 0 680 200" class="circuit-svg" xmlns="http://www.w3.org/2000/svg">
        <rect width="680" height="200" fill="#0f172a" rx="8" />
        ${statusWatermark}
        <!-- ESP32 Module -->
        <rect x="30" y="35" width="160" height="130" rx="6" fill="#1e293b" stroke="#475569" stroke-width="2" />
        <rect x="30" y="35" width="160" height="24" rx="6" fill="#334155" />
        <text x="110" y="51" fill="#f8fafc" font-size="12" font-weight="bold" text-anchor="middle">ESP32 DevKit</text>
        <circle cx="175" cy="75" r="5" fill="#ef4444" />
        <text x="165" y="79" fill="#f8fafc" font-size="11" text-anchor="end" font-family="monospace">3V3</text>
        <circle cx="175" cy="110" r="5" fill="#f59e0b" />
        <text x="165" y="114" fill="#f8fafc" font-size="10" text-anchor="end" font-family="monospace">GPIO 34 (ADC1)</text>
        <circle cx="175" cy="145" r="5" fill="#38bdf8" />
        <text x="165" y="149" fill="#f8fafc" font-size="11" text-anchor="end" font-family="monospace">GND</text>

        <!-- LDR & Resistor Divider -->
        <rect x="280" y="45" width="130" height="50" rx="4" fill="#1e293b" stroke="#f59e0b" stroke-width="1.5" />
        <text x="345" y="66" fill="#fde047" font-size="11" font-weight="bold" text-anchor="middle">Photoresistor (LDR)</text>
        <text x="345" y="82" fill="#94a3b8" font-size="8" text-anchor="middle">Light-sensitive</text>

        <rect x="280" y="120" width="130" height="50" rx="4" fill="#1e293b" stroke="#38bdf8" stroke-width="1.5" />
        <text x="345" y="141" fill="#7dd3fc" font-size="11" font-weight="bold" text-anchor="middle">10 kΩ Resistor</text>
        <text x="345" y="157" fill="#94a3b8" font-size="8" text-anchor="middle">Pull-down leg</text>

        <!-- Divider Junction Point -->
        <circle cx="480" cy="110" r="6" fill="#f59e0b" />
        <text x="495" y="114" fill="#fde047" font-size="10" font-weight="bold">Divider Junction</text>

        <!-- Wires -->
        <path d="M 175 75 C 220 75, 240 70, 280 70" stroke="#ef4444" stroke-width="3" fill="none" />
        <line x1="410" y1="70" x2="480" y2="110" stroke="#ef4444" stroke-width="2.5" />
        <line x1="410" y1="145" x2="480" y2="110" stroke="#38bdf8" stroke-width="2.5" />
        <path d="M 480 110 C 350 110, 240 110, 175 110" stroke="#f59e0b" stroke-width="3" fill="none" />
        <text x="210" y="103" fill="#fde047" font-size="9" font-weight="bold">Analog In (Yellow)</text>
        <path d="M 280 145 C 230 145, 210 145, 175 145" stroke="#38bdf8" stroke-width="3" fill="none" />
      </svg>
    `;
  }

  // Fallback for transistor-driver or generic
  return `
    <svg viewBox="0 0 680 160" class="circuit-svg" xmlns="http://www.w3.org/2000/svg">
      <rect width="680" height="160" fill="#0f172a" rx="8" />
      ${statusWatermark}
      <text x="340" y="65" fill="#f8fafc" font-size="14" font-weight="bold" text-anchor="middle">${guide.label || "Circuit Reference Layout"}</text>
      <text x="340" y="88" fill="#94a3b8" font-size="11" text-anchor="middle">${guide.breadboard_layout_summary || "See connection table below for exact pinout netlist."}</text>
      <text x="340" y="115" fill="#ef4444" font-size="10" font-weight="bold" text-anchor="middle">⚡ MANDATORY: KEEP ALL POWER SUPPLIES DISCONNECTED BEFORE WIRING</text>
    </svg>
  `;
}

async function analyzePhoto() {
  if (!selectedFile) return;
  analyzeButton.disabled = true;
  analyzeButton.textContent = "Analyzing with Ollama…";
  setMessage("");

  const fileToSend = await scaleImageFile(selectedFile, 800, 0.75);
  const formData = new FormData();
  formData.append("image", fileToSend);
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: formData,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not analyze this photo.");
    const components = result.components || [];
    if (components.length === 0) {
      setMessage("No components clear enough to identify. Try a closer, brighter photo.");
      return;
    }
    // Automatically add to shared inventory as unconfirmed
    await fetch("/api/inventory", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
      body: JSON.stringify({ items: components, source: "photo upload" }),
    });
    await loadInventory();
    setMessage(`Added ${components.length} component${components.length === 1 ? "" : "s"} to your inventory review.`);
  } catch (error) {
    setMessage(error.message || "Could not reach local Ollama. Check that it is running.");
  } finally {
    analyzeButton.disabled = !selectedFile;
    analyzeButton.innerHTML = "<span aria-hidden=\"true\">🔍</span> Identify visible parts";
  }
}

// Event Listeners
fileInput.addEventListener("change", () => setSelectedFile(fileInput.files[0]));
analyzeButton.addEventListener("click", analyzePhoto);
document.querySelector("#change-photo").addEventListener("click", () => fileInput.click());
document.querySelector("#add-part").addEventListener("click", addManualPart);
clearInventoryBtn.addEventListener("click", clearAllInventory);

for (const eventName of ["dragenter", "dragover"]) {
  uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.classList.add("is-dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.classList.remove("is-dragging");
  });
}
uploadZone.addEventListener("drop", (event) => setSelectedFile(event.dataTransfer.files[0]));

// Initialize
checkService();
loadProjects().then(loadInventory);

// Background poll for shared inventory sync (e.g. from phone captures)
setInterval(loadInventory, 4000);
