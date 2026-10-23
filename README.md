# BoardBuddy

BoardBuddy is a local-first electronics lab assistant for reviewing component photos and preparing a simple ESP32 temperature-monitor build. It sends supported photos only to Ollama running on `localhost` with `gemma3:4b`; it has no cloud image service or analytics.

## Project status

**BoardBuddy is an early-stage prototype.** The current implementation focuses on local AI-assisted component photo review and a simple ESP32 temperature-monitor checklist. The broader vision—persistent project memory, structured circuit validation, voice-guided tutoring, document ingestion, and hardware diagnostics—is planned work, not a claim that those capabilities are already implemented.

### Product documentation

- [Product Requirements Document (PRD)](docs/PRD.md) — users, problem, MVP scope, success criteria and risks.
- [Software Requirements Specification (SRS)](docs/SRS.md) — functional/non-functional requirements, interfaces, evidence model and acceptance criteria.
- [Architecture overview](docs/ARCHITECTURE.md) — current prototype boundaries and proposed evolution.
- [Roadmap to the December 2026 hackathon](docs/ROADMAP.md) — phased plan and demo goals.

### Current capability boundaries

| Area | Current status |
|---|---|
| Local Flask web app and authentication | Implemented in public prototype; validate against local checkout |
| Local Ollama image analysis | Implemented; quality and latency depend on local setup |
| Component suggestions with evidence/uncertainty | AI suggestions; user review required |
| ESP32 reference checklist | Implemented as a narrow prototype flow |
| Persistent project memory / SQLite workspace | Planned |
| Canonical circuit netlist and design-level electrical rules | Planned |
| Automated pin/wire recognition and visual hole overlays | Experimental idea; not validated |
| Physical electrical verification | Not provided by camera/AI alone |
| Universal multi-domain project building | Long-term vision, not current functionality |

BoardBuddy prioritizes **evidence over unsupported claims**. A model suggestion is not proof that a component is present; an inventory checklist is not proof that wiring is correct; a design-level software check cannot guarantee a physical circuit is safe. Inspect the actual hardware and follow the component/board datasheets before applying power.

## AI Model and Terms

BoardBuddy uses Google's Gemma 3 model, `gemma3:4b`, through Ollama for local image analysis. The model's terms are separate from BoardBuddy's MIT licence.

- Official Gemma terms: [https://ai.google.dev/gemma/terms](https://ai.google.dev/gemma/terms) (verified official and reachable)
- Ollama website: [https://ollama.com/](https://ollama.com/) (verified official and reachable)

## Requirements

- Windows 10/11 and Python 3.14 (the interpreter available during this setup) or another Flask-compatible Python release.
- Ollama for Windows, running locally, with the `gemma3:4b` model pulled.
- Flask and `requests` (installed in the project virtual environment by the steps below).
- A temporary password of at least 16 characters and a random session-signing key of at least 32 characters, supplied through environment variables.

## Setup and launch (Windows PowerShell)

From the `BoardBuddy-Work` directory, open PowerShell and run:

```powershell
Set-Location .\BoardBuddy-App
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ollama pull gemma3:4b

# Generate a new secret signing key and temporary password for this PowerShell session.
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$secretBytes = New-Object byte[] 48
$rng.GetBytes($secretBytes)
$env:BOARDBUDDY_SECRET_KEY = [Convert]::ToBase64String($secretBytes)
$passwordBytes = New-Object byte[] 24
$rng.GetBytes($passwordBytes)
$env:BOARDBUDDY_PASSWORD = [Convert]::ToBase64String($passwordBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$rng.Dispose()
Write-Host "Temporary BoardBuddy password (share privately): $env:BOARDBUDDY_PASSWORD"

# Keep Flask on this computer only. The app rejects a non-loopback bind without TLS.
$env:BOARDBUDDY_HOST = "127.0.0.1"
$env:BOARDBUDDY_PORT = "5000"
python app.py
```

If PowerShell blocks virtual-environment activation, allow it for this terminal only, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

If Ollama is not already running, start it from another PowerShell window with `ollama serve`. Keep it running while using BoardBuddy. Open <http://localhost:5000> in a browser and sign in with the temporary password printed above. Stop the Flask server with `Ctrl+C`.

The password and signing key exist only in the PowerShell process environment; keep this shell open while the app runs. Share the temporary password only through a private channel with the intended user. Starting a new shell requires generating and setting both values again; changing the signing key invalidates all existing sessions. Do not put either secret in source files, command-line arguments, screenshots, or a public channel. The app refuses to start if either secret is missing or below its minimum length.

Open the phone-camera workflow at <http://127.0.0.1:5000/camera> when using the same computer. Browser camera permission is available on secure contexts such as localhost; a phone visiting the laptop over plain LAN HTTP is **not** a secure context.

The Python executable path above matches the interpreter detected on the development machine. If Python is installed elsewhere, replace that one path with the location of your Python 3.14 executable, or use `py -3.14 -m venv .venv` when the Windows Python launcher is configured.

## Use

1. Choose a JPEG, PNG, or WebP photo from the browser or take one with a mobile device. Images are limited to 8 MiB.
2. Ask the local model for visible component suggestions. The model returns a component name, visible evidence, and an uncertainty note; it does not provide calibrated confidence percentages.
3. Edit names as needed and tick **I confirm** only after checking the parts yourself. **Add part** lets you enter a part that was missed by image analysis.
4. Compare the confirmed list with the ESP32 temperature-monitor checklist. Unconfirmed suggestions are marked **Uncertain**; parts without a matching reviewed suggestion are **Missing**. These are photo-review states, not proof that hardware is physically present or absent.

## Safety

Disconnect power before wiring. Check the exact ESP32 board and sensor pinouts and datasheets; pin arrangements vary. Never use mains voltage. The project uses the board's 3.3 V pin, a DHT22/AM2302 sensor, and GPIO 4. A bare DHT22 may need a 10 kΩ DATA-to-3V3 pull-up; some modules include one. Inspect your own module before adding it.

**BoardBuddy does not electrically verify a real circuit.** Its visual suggestions and checklist are educational aids, not a substitute for inspection or measurement. Do not connect power until you have independently checked every connection.

## Local checks

With the virtual environment active:

```powershell
python -m unittest discover -s tests -v
```

The checks use mocked Ollama replies and do not require sending a photo to the model. To test real image analysis, start Ollama and confirm `gemma3:4b` is installed first.

## Authentication and session security

The login page is the only non-static application route available without signing in. `/`, `/camera`, `/api/health`, `/api/analyze`, and `/logout` require an authenticated session. Page requests redirect to `/login`; API requests return HTTP 401. Login, logout, and image-analysis POSTs require a CSRF token. The session cookie is `Secure`, `HttpOnly`, and `SameSite=Lax`; login sessions expire after eight hours. Five failed password attempts from the same client address within 15 minutes trigger a five-minute lockout.

Sessions and login-throttle counters are held in process memory. They are intentionally suitable only for this single-process hackathon app: restarting Flask logs everyone out and clears throttles; multiple worker processes would not share the session store. Do not put this development server on a public interface or configure a tunnel yet. Review the access-control and deployment setup before any remote-access test. Ollama remains fixed at `http://localhost:11434` and must not be exposed through a proxy or tunnel.

## Use a phone camera over trusted HTTPS

`getUserMedia()` is restricted to secure browser contexts. BoardBuddy binds to `127.0.0.1` over HTTP by default. Do not change that to `0.0.0.0` and assume `http://<laptop-ip>` will allow camera access: browsers normally block it, and this app refuses a non-loopback bind without a TLS certificate and key.

For development on a trusted private Wi-Fi network, use [mkcert](https://github.com/FiloSottile/mkcert) to create a locally trusted certificate containing the laptop's Wi-Fi IPv4 address. Install mkcert on Windows with Chocolatey or Scoop (use one):

```powershell
choco install mkcert
```

```powershell
scoop bucket add extras
scoop install mkcert
```

In PowerShell, replace `192.168.1.42` with the laptop's Wi-Fi IPv4 address shown by `ipconfig`:

```powershell
mkcert -install
$lanIp = "192.168.1.42"
$certDir = Join-Path $env:LOCALAPPDATA "BoardBuddy\certs"
New-Item -ItemType Directory -Force $certDir | Out-Null
mkcert -cert-file "$certDir\boardbuddy.pem" -key-file "$certDir\boardbuddy-key.pem" localhost 127.0.0.1 $lanIp
```

Trust mkcert's public root CA on the phone before opening the HTTPS page. Find its folder with `mkcert -CAROOT`; transfer **only** `rootCA.pem` to the phone. Never copy or share `rootCA-key.pem` or the generated `boardbuddy-key.pem`.

- On iPhone/iPad, install the transferred certificate profile, then enable full trust for it under **Settings > General > About > Certificate Trust Settings**.
- On Android, install it as a CA certificate in the device security/credential settings. Menu names and browser trust behavior vary by Android version. If the browser still shows a certificate warning, do not bypass it; use the upload fallback or a device/browser with a trusted certificate.

Stop the loopback Flask server, then start BoardBuddy in the same PowerShell session with the certificate settings. Bind specifically to the laptop's Wi-Fi IPv4 address rather than `0.0.0.0`, so Flask does not listen on other network interfaces:

```powershell
$env:BOARDBUDDY_HOST = $lanIp
$env:BOARDBUDDY_PORT = "5000"
$env:BOARDBUDDY_TLS_CERT = "$certDir\boardbuddy.pem"
$env:BOARDBUDDY_TLS_KEY = "$certDir\boardbuddy-key.pem"
python app.py
```

Allow Python through Windows Defender Firewall for **Private networks only** if prompted. Keep the phone and laptop on the same trusted Wi-Fi network, then open `https://192.168.1.42:5000/camera` on the phone, using your actual `$lanIp`. The certificate must match that address and be trusted by the phone. BoardBuddy sends the captured JPEG over HTTPS to the laptop; the laptop then contacts only its own `http://localhost:11434` Ollama service. No public tunnel or external image service is used. If camera permission is unavailable, choose a photo in the page's upload fallback.

The camera page can analyze one frame on demand. Its optional periodic mode waits the configured interval **after** each model response before capturing the next frame, so Ollama requests do not overlap. Stop periodic analysis or the camera at any time to release the phone camera.
