# REYDM Desktop (PyQt6)

A native desktop edition of the **REYDM – REY Datamind Platform**.

This is the same Flask + MySQL application you already have, wrapped in a
native PyQt6 window using QtWebEngine. The web server runs **locally** in the
background (no external browser, no address bar) and the UI is rendered in a
real desktop window. All nine tools work exactly as before:

> Reminder · Night Shift · Char Palette · Cost Converter · Project Analysis ·
> PDF Unlocker · Attendance · Petty Cash (CBE / DGL) · Leave Manager

---

## Why this approach is fast & efficient

- **Local-only serving** — pages are served from `127.0.0.1`, so there are no
  network round-trips for the UI. It feels instant.
- **Production WSGI server** — uses `waitress` (multi-threaded) instead of
  Flask's slow single-threaded dev server. No debug reloader overhead.
- **Single process** — one Python process runs both the server and the window.
- **Zero rewrite** — keeps 100% of your existing features, templates, and JS,
  so there's nothing to re-test or re-implement.

---

## 1. Quick start (run from source)

### Prerequisites
- Python **3.9 – 3.12** (3.11 recommended)
- Internet access **only** for the cloud MySQL database (the UI itself is local)

### Steps
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) configure database / email
cp .env.example .env        # then edit .env with your credentials

# 3. Launch the desktop app
python desktop_app.py
```

A native **REYDM** window opens. Log in with your existing credentials
(default admin: `admin@system.local` / `admin123`).

> On Linux you may also need system Qt/WebEngine libraries:
> `sudo apt install libxcb-cursor0 libnss3 libxkbcommon0`

---

## 2. Configuration

All settings come from environment variables or a `.env` file in this folder
(same keys as the original app):

| Variable | Purpose | Default |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | MySQL connection | Aiven cloud (from `.env.example`) |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Email notifications | — |
| `SMTP_MODE` | `ssl` (465) or `starttls` (587) | `ssl` |
| `SECRET_KEY` | Flask session key | dev fallback |
| `APP_PORT` | Local server port (auto-picks a free one if taken) | `5000` |
| `REYDM_FORCE_GPU` | Set to `1` to force hardware GPU rendering | `0` (software fallback) |

The desktop app auto-loads `.env` if present.

---

## 3. Build a standalone executable (no Python needed on the target PC)

A ready-to-use **PyInstaller** spec is included.

```bash
pip install -r requirements.txt          # includes pyinstaller
pyinstaller REYDM.spec
```

Output:
```
dist/REYDM/REYDM.exe      ← Windows
dist/REYDM/REYDM          ← macOS / Linux
```

Distribute the **entire `dist/REYDM/` folder**. Double-clicking `REYDM`
launches the app — no Python required on that machine.

> **Tip:** to debug a build, open `REYDM.spec` and set `console=True`. That
> shows the Flask/waitress logs in a console window. Set it back to `False`
> for the final distributable.

> **Cross-platform note:** PyInstaller does not cross-compile. Build the
> Windows `.exe` on Windows, the macOS app on macOS, etc.

---

## 4. Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `F5` / `Ctrl+R` | Reload |
| `Alt+←` / `Alt+→` | Back / Forward |
| `Ctrl+ +` / `Ctrl+ -` | Zoom in / out |
| `Ctrl+0` | Reset zoom |

File exports (CSV, unlocked PDFs, etc.) open a native **Save As** dialog.

---

## 5. Project layout

```
REYDM_Desktop/
├── desktop_app.py        ← PyQt6 launcher (the new entry point)
├── REYDM.spec            ← PyInstaller build config
├── app.py                ← your existing Flask app (unchanged)
├── templates/            ← Jinja2 templates (unchanged)
├── static/               ← CSS / JS / images (unchanged)
├── database_setup.sql    ← schema
├── requirements.txt      ← all dependencies
├── .env.example          ← config template
├── run.sh                ← convenience launcher (macOS/Linux)
└── run.bat               ← convenience launcher (Windows)
```

---

## 6. Troubleshooting

**Blank/white window** — usually a GPU driver issue. The app already falls
back to software rendering by default; if you forced GPU mode, unset
`REYDM_FORCE_GPU`.

**"could not start its local server"** — almost always the database. Check
internet access and your `DB_*` values in `.env`. The app needs to reach the
MySQL host.

**Port already in use** — the launcher auto-selects a free port, so this is
handled automatically. You can also set `APP_PORT` explicitly.

**Login/session not persisting** — sessions are in-memory per run, same as the
original Flask app's default behaviour.
