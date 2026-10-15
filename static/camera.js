const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_FRAME_EDGE = 1600;
const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const video = document.querySelector("#live-video");
const placeholder = document.querySelector("#camera-placeholder");
const liveIndicator = document.querySelector("#live-indicator");
const secureWarning = document.querySelector("#secure-warning");
const startButton = document.querySelector("#start-camera");
const stopButton = document.querySelector("#stop-camera");
const captureButton = document.querySelector("#capture-analyze");
const periodicButton = document.querySelector("#periodic-toggle");
const intervalSelect = document.querySelector("#interval-seconds");
const cameraMessage = document.querySelector("#camera-message");
const fallbackInput = document.querySelector("#fallback-image");
const fallbackName = document.querySelector("#fallback-name");
const uploadButton = document.querySelector("#upload-analyze");
const analysisMessage = document.querySelector("#analysis-message");
const resultsList = document.querySelector("#camera-results-list");

let cameraStream = null;
let fallbackFile = null;
let analysisInFlight = false;
let periodicActive = false;
let periodicTimer = null;

function setCameraMessage(message, kind = "info") {
  cameraMessage.textContent = message;
  cameraMessage.dataset.kind = kind;
}

function updateControls() {
  const cameraReady = Boolean(cameraStream);
  startButton.disabled = cameraReady || !window.isSecureContext || !navigator.mediaDevices?.getUserMedia;
  stopButton.disabled = !cameraReady;
  captureButton.disabled = !cameraReady || analysisInFlight;
  intervalSelect.disabled = !cameraReady || periodicActive;
  periodicButton.disabled = !cameraReady && !periodicActive;
  periodicButton.textContent = periodicActive ? "Stop periodic analysis" : "Start periodic analysis";
  uploadButton.disabled = !fallbackFile || analysisInFlight;
}

function explainCameraAvailability() {
  if (!window.isSecureContext) {
    secureWarning.hidden = false;
    setCameraMessage("Camera is blocked because this page is not a secure context. Use trusted HTTPS or choose a photo below.", "error");
  } else if (!navigator.mediaDevices?.getUserMedia) {
    secureWarning.hidden = false;
    setCameraMessage("This browser does not expose camera access here. Choose a photo below instead.", "error");
  } else {
    secureWarning.hidden = true;
    setCameraMessage("Camera permission is available on this trusted origin. The preview stays in this page until you analyze a frame.");
  }
  updateControls();
}

function stopPeriodicAnalysis() {
  periodicActive = false;
  if (periodicTimer !== null) window.clearTimeout(periodicTimer);
  periodicTimer = null;
  updateControls();
}

function stopCamera() {
  stopPeriodicAnalysis();
  if (cameraStream) {
    for (const track of cameraStream.getTracks()) track.stop();
  }
  cameraStream = null;
  video.srcObject = null;
  video.hidden = true;
  placeholder.hidden = false;
  liveIndicator.hidden = true;
  setCameraMessage("Camera stopped.");
  updateControls();
}

async function startCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    explainCameraAvailability();
    return;
  }
  startButton.disabled = true;
  setCameraMessage("Waiting for camera permission…");
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    video.srcObject = cameraStream;
    video.hidden = false;
    placeholder.hidden = true;
    liveIndicator.hidden = false;
    await video.play();
    setCameraMessage("Rear camera ready. Frame the parts, then capture one still image.", "success");
  } catch (error) {
    if (cameraStream) {
      for (const track of cameraStream.getTracks()) track.stop();
    }
    cameraStream = null;
    video.srcObject = null;
    video.hidden = true;
    placeholder.hidden = false;
    liveIndicator.hidden = true;
    const messages = {
      NotAllowedError: "Camera permission was denied. Allow access in your browser settings or choose a photo below.",
      NotFoundError: "No camera was found. Choose a photo below instead.",
      NotReadableError: "The camera is busy in another app. Close that app or choose a photo below.",
      OverconstrainedError: "The rear camera could not be selected. Try the photo-upload option below.",
    };
    setCameraMessage(messages[error.name] || `Could not start the camera: ${error.message || "unknown error"}. Choose a photo below.`, "error");
  }
  updateControls();
}

function captureCanvasBlob(canvas, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("The browser could not encode this camera frame."));
    }, "image/jpeg", quality);
  });
}

async function captureFrameFile() {
  const sourceWidth = video.videoWidth;
  const sourceHeight = video.videoHeight;
  if (!cameraStream || sourceWidth < 1 || sourceHeight < 1) {
    throw new Error("Wait for the camera preview to become ready, then try again.");
  }

  const scale = Math.min(1, MAX_FRAME_EDGE / Math.max(sourceWidth, sourceHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(sourceWidth * scale);
  canvas.height = Math.round(sourceHeight * scale);
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("This browser could not prepare the camera frame.");
  context.drawImage(video, 0, 0, canvas.width, canvas.height);

  let blob = await captureCanvasBlob(canvas, 0.86);
  if (blob.size > MAX_IMAGE_BYTES) {
    const smallerScale = Math.min(0.75, 1200 / Math.max(canvas.width, canvas.height));
    canvas.width = Math.max(1, Math.round(canvas.width * smallerScale));
    canvas.height = Math.max(1, Math.round(canvas.height * smallerScale));
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    blob = await captureCanvasBlob(canvas, 0.72);
  }
  if (blob.size > MAX_IMAGE_BYTES) throw new Error("This frame is still over 8 MiB. Move farther from the parts and try again.");

  return new File([blob], `boardbuddy-frame-${Date.now()}.jpg`, { type: "image/jpeg" });
}

function appendText(parent, tag, text, className = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  parent.append(element);
  return element;
}

function renderComponents(components) {
  resultsList.replaceChildren();
  if (components.length === 0) {
    analysisMessage.textContent = "No components were clear enough to identify in this frame. Try closer, brighter framing.";
    return;
  }
  analysisMessage.textContent = `${components.length} suggestion${components.length === 1 ? "" : "s"}. Check each part yourself; these are not calibrated confidence scores.`;
  for (const component of components) {
    const card = document.createElement("article");
    card.className = "camera-component";
    appendText(card, "h3", component.name);
    const evidence = appendText(card, "p", "");
    evidence.append(document.createTextNode("Visible evidence: "));
    evidence.append(document.createTextNode(component.evidence));
    const uncertainty = appendText(card, "p", "");
    uncertainty.append(document.createTextNode("Uncertainty: "));
    uncertainty.append(document.createTextNode(component.uncertainty));
    resultsList.append(card);
  }
}

async function analyzeFile(file, sourceLabel) {
  if (analysisInFlight) {
    setCameraMessage("An Ollama request is already running. This frame was skipped to avoid overlapping requests.", "error");
    return false;
  }
  analysisInFlight = true;
  updateControls();
  analysisMessage.textContent = `Sending ${sourceLabel} to local Ollama…`;
  setCameraMessage("Analyzing with the local gemma3:4b model. Larger requests can take a while.");
  const formData = new FormData();
  formData.append("image", file, file.name || "boardbuddy-upload");
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: formData,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The local model could not analyze this image.");
    document.querySelector("#model-tag").textContent = result.model || "gemma3:4b";
    renderComponents(result.components || []);
    setCameraMessage("Analysis complete. Results came from Ollama on this laptop.", "success");
    return true;
  } catch (error) {
    analysisMessage.textContent = "No model response for this image.";
    setCameraMessage(error.message || "Could not reach local Ollama. Check that it is running and try again.", "error");
    return false;
  } finally {
    analysisInFlight = false;
    updateControls();
  }
}

async function analyzeCurrentFrame() {
  try {
    const file = await captureFrameFile();
    await analyzeFile(file, "camera frame");
  } catch (error) {
    setCameraMessage(error.message || "Could not capture a camera frame.", "error");
    return false;
  }
  return true;
}

async function runPeriodicTick() {
  if (!periodicActive) return;
  await analyzeCurrentFrame();
  if (periodicActive) {
    periodicTimer = window.setTimeout(runPeriodicTick, Number(intervalSelect.value) * 1000);
  }
}

function togglePeriodicAnalysis() {
  if (periodicActive) {
    stopPeriodicAnalysis();
    setCameraMessage("Periodic analysis stopped. Any current Ollama request will finish.");
    return;
  }
  if (!cameraStream) {
    setCameraMessage("Start the rear camera before starting periodic analysis.", "error");
    return;
  }
  periodicActive = true;
  updateControls();
  setCameraMessage(`Periodic analysis started. The next frame runs ${intervalSelect.value} seconds after each reply.`, "success");
  runPeriodicTick();
}

function selectFallbackFile(file) {
  if (!file) return;
  if (file.type && !ALLOWED_TYPES.has(file.type)) {
    setCameraMessage("Choose a JPEG, PNG, or WebP image.", "error");
    fallbackInput.value = "";
    fallbackFile = null;
  } else if (file.size > MAX_IMAGE_BYTES) {
    setCameraMessage("That photo is over the 8 MiB limit. Choose a smaller image.", "error");
    fallbackInput.value = "";
    fallbackFile = null;
  } else {
    fallbackFile = file;
    fallbackName.textContent = file.name;
    setCameraMessage("Photo selected. It will be sent only to this laptop’s local Ollama service.");
  }
  updateControls();
}

startButton.addEventListener("click", startCamera);
stopButton.addEventListener("click", stopCamera);
captureButton.addEventListener("click", analyzeCurrentFrame);
periodicButton.addEventListener("click", togglePeriodicAnalysis);
fallbackInput.addEventListener("change", () => selectFallbackFile(fallbackInput.files[0]));
uploadButton.addEventListener("click", () => {
  if (fallbackFile) analyzeFile(fallbackFile, "selected photo");
});
window.addEventListener("pagehide", stopCamera);

explainCameraAvailability();
updateControls();
