const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const requiredParts = [
  { id: "esp32", name: "ESP32 DevKit board", quantity: "1" },
  { id: "dht22", name: "DHT22 / AM2302 sensor", quantity: "1" },
  { id: "breadboard", name: "Breadboard", quantity: "1" },
  { id: "jumpers", name: "Male-to-male jumper wires", quantity: "3+" },
  { id: "usb", name: "USB data cable for your board", quantity: "1" },
];

const buildSteps = [
  "Unplug the ESP32 from USB and disconnect every power source before making connections.",
  "Identify the exact ESP32 board and DHT22 module. Check their labels and datasheets for the pin order; sensor modules can differ.",
  "Place the ESP32 and sensor on the breadboard with the pins in separate rows. Keep the sensor within easy reach for testing.",
  "With power still disconnected, connect sensor VCC to the board’s 3V3 pin, GND to GND, and DATA to GPIO 4. Leave any NC pin unconnected.",
  "For a bare DHT22 without a built-in pull-up, add a 10 kΩ resistor between DATA and 3V3. Do not add a second pull-up if the module already has one.",
  "Inspect every connection against the exact pinout. Only after checking, power the ESP32 from its USB data cable; do not connect this project to mains voltage.",
];

const fileInput = document.querySelector("#image-input");
const uploadZone = document.querySelector("#upload-zone");
const previewWrap = document.querySelector("#preview-wrap");
const previewImage = document.querySelector("#image-preview");
const fileName = document.querySelector("#file-name");
const analyzeButton = document.querySelector("#analyze-button");
const uploadMessage = document.querySelector("#upload-message");
const serviceStatus = document.querySelector("#service-status");
const serviceStatusText = document.querySelector("#service-status-text");
const suggestionsList = document.querySelector("#suggestions-list");
const partsList = document.querySelector("#parts-list");
const projectSteps = document.querySelector("#project-steps");

let selectedFile = null;
let previewUrl = null;
let nextPartId = 1;
let suggestions = [];

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

function renderSuggestions() {
  suggestionsList.replaceChildren();
  if (suggestions.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const mark = addText(empty, "span", "empty-mark", "⌁");
    mark.setAttribute("aria-hidden", "true");
    addText(empty, "p", "", "Your reviewed parts will show up here.");
    suggestionsList.append(empty);
  } else {
    suggestions.forEach((part) => {
      const card = document.createElement("article");
      card.className = `component-card${part.confirmed ? " is-confirmed" : ""}`;
      const head = document.createElement("div");
      head.className = "component-head";
      const nameInput = document.createElement("input");
      nameInput.className = "component-name";
      nameInput.type = "text";
      nameInput.maxLength = 120;
      nameInput.value = part.name;
      nameInput.setAttribute("aria-label", "Component name; edit to correct the identification");
      nameInput.addEventListener("input", () => {
        part.name = nameInput.value;
        renderChecklist();
      });
      head.append(nameInput);

      const confirmLabel = document.createElement("label");
      confirmLabel.className = "confirm-label";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = part.confirmed;
      checkbox.setAttribute("aria-label", `Confirm ${part.name || "this part"}`);
      checkbox.addEventListener("change", () => {
        part.confirmed = checkbox.checked;
        renderSuggestions();
        renderChecklist();
      });
      confirmLabel.append(checkbox, document.createTextNode("I confirm"));
      head.append(confirmLabel);

      const removeButton = document.createElement("button");
      removeButton.className = "remove-part";
      removeButton.type = "button";
      removeButton.textContent = "×";
      removeButton.setAttribute("aria-label", `Remove ${part.name || "this part"}`);
      removeButton.addEventListener("click", () => {
        suggestions = suggestions.filter((suggestion) => suggestion.id !== part.id);
        renderSuggestions();
        renderChecklist();
      });
      head.append(removeButton);
      card.append(head);
      addText(card, "p", "component-detail", "Visible evidence: ");
      card.lastElementChild.append(document.createTextNode(part.evidence));
      addText(card, "p", "component-detail", "Uncertainty: ");
      card.lastElementChild.append(document.createTextNode(part.uncertainty));
      suggestionsList.append(card);
    });
  }
  renderChecklist();
}

function partMatch(partId, name) {
  const text = name.toLowerCase().replace(/[–—]/g, "-");
  const rules = {
    esp32: ["esp32", "esp-32"],
    dht22: ["dht22", "am2302"],
    breadboard: ["breadboard", "solderless board"],
    jumpers: ["jumper", "dupont wire", "dupont cable"],
    usb: ["usb cable", "usb-c cable", "micro-usb cable", "type-c cable"],
  };
  return rules[partId].some((alias) => text.includes(alias));
}

function renderChecklist() {
  partsList.replaceChildren();
  for (const required of requiredParts) {
    const candidates = suggestions.filter((part) => part.name.trim() && partMatch(required.id, part.name));
    const confirmed = candidates.some((part) => part.confirmed);
    const status = confirmed ? "confirmed" : candidates.length ? "uncertain" : "missing";
    const labels = { confirmed: "Confirmed", uncertain: "Uncertain", missing: "Missing" };
    const row = document.createElement("li");
    row.className = "part-row";
    const nameGroup = document.createElement("span");
    nameGroup.className = "part-name";
    nameGroup.append(document.createTextNode(required.name));
    const quantity = addText(nameGroup, "span", "part-quantity", ` · ${required.quantity}`);
    quantity.setAttribute("aria-label", `quantity ${required.quantity}`);
    const statusNode = addText(row, "span", `part-status status-${status}`, labels[status]);
    statusNode.setAttribute("aria-label", `${required.name}: ${labels[status]}`);
    row.prepend(nameGroup);
    partsList.append(row);
  }
}

async function analyzePhoto() {
  if (!selectedFile) return;
  analyzeButton.disabled = true;
  analyzeButton.textContent = "Looking closely…";
  setMessage("");
  const formData = new FormData();
  formData.append("image", selectedFile);
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: formData,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not analyze this photo.");
    suggestions = result.components.map((part) => ({ ...part, id: nextPartId++, confirmed: false }));
    renderSuggestions();
    if (suggestions.length === 0) setMessage("No components were clear enough to suggest. Try a closer, brighter photo.");
  } catch (error) {
    setMessage(error.message || "Could not reach the local model. Check Ollama and try again.");
  } finally {
    analyzeButton.disabled = !selectedFile;
    analyzeButton.innerHTML = "<span aria-hidden=\"true\">✳</span> Identify visible parts";
  }
}

fileInput.addEventListener("change", () => setSelectedFile(fileInput.files[0]));
analyzeButton.addEventListener("click", analyzePhoto);
document.querySelector("#change-photo").addEventListener("click", () => fileInput.click());
document.querySelector("#add-part").addEventListener("click", () => {
  suggestions.push({
    id: nextPartId++,
    name: "",
    evidence: "Added by you. Check the part markings and package.",
    uncertainty: "Confirm its identity and pinout from the component label or datasheet.",
    confirmed: false,
  });
  renderSuggestions();
  suggestionsList.lastElementChild.querySelector("input[type='text']").focus();
});

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

for (const required of requiredParts) {
  const row = document.createElement("li");
  row.className = "part-row";
  addText(row, "span", "part-name", required.name);
  addText(row, "span", "part-status status-missing", "Missing");
  partsList.append(row);
}
for (const step of buildSteps) addText(projectSteps, "li", "", step);
checkService();
renderSuggestions();
