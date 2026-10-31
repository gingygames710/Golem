# Golem

**Your own AI agent that lives on your PC.** You build it, give it a brain (any local model via
[Ollama](https://ollama.com), or Claude via the API) and a personality, and it acts on your computer for you —
running commands, reading and writing files, browsing the web, controlling the screen, generating images and
sound, and more. It runs locally and free; nothing leaves your machine unless you choose a web search, a Claude
API brain, or phone notifications.

Golem is three apps behind one installer: the **Builder** (set it up with checkboxes), the **agent window** (chat
with it), and the **core** (the agent itself). The model only ever *asks* to use a tool — your side decides what
actually runs.

> 🟢 **New to this? Never used an AI before?** Read **[GETTING-STARTED.md](GETTING-STARTED.md)** — it's three
> steps, no jargon. Short version: install, open Golem Builder, click the blue **⚡ Easy Mode** button, done.

## Contents

- [Safety and dangers (read this first)](#safety-and-dangers-read-this-first)
- [What you need](#what-you-need)
- [Quick start](#quick-start)
- [Keeping Golem up to date](#keeping-golem-up-to-date)
- [The three apps](#the-three-apps)
- [How it works](#how-it-works)
- [The Builder](#the-builder)
- [Profiles](#profiles)
- [Tools the agent has](#tools-the-agent-has)
- [Choosing a model](#choosing-a-model)
- [Personality](#personality)
- [Appearance](#appearance)
- [Voice](#voice)
- [The ambient orb](#the-ambient-orb)
- [Limiting it to folders](#limiting-it-to-folders)
- [Hardened Mode (for cautious users)](#hardened-mode-for-cautious-users)
- [Sandbox Mode (a real wall)](#sandbox-mode-a-real-wall)
- [Using it from your phone](#using-it-from-your-phone)
- [Phone notifications](#phone-notifications)
- [Running on a schedule](#running-on-a-schedule)
- [Long unattended runs](#long-unattended-runs)
- [Add-on: images and sound](#add-on-images-and-sound)
- [Add-on: reverse engineering (REA)](#add-on-reverse-engineering-rea)
- [Godot game-making](#godot-game-making)
- [The config file](#the-config-file)
- [Running and building from source](#running-and-building-from-source)
- [Project layout](#project-layout)
- [Troubleshooting & FAQ](#troubleshooting--faq)

## Safety and dangers (read this first)

Golem can do real things to your computer. Treat it like handing someone remote hands on your machine, and
understand these before you turn features on:

- **It runs commands, and reads, writes and deletes files.** With screen control on, it also moves your mouse and
  types. A wrong or confused step can damage or delete data. **Keep backups.**
- **Permission prompts are your safety gate.** In normal chat it asks before anything that changes your machine —
  *read what it wants to do and don't approve blindly.*
- **Auto-approve removes that gate.** `--yes`, the phone web app (`--serve`), overnight runs and scheduled jobs run
  tools *without asking*. Only use them for work and folders you're fine with it touching while you're away.
- **The folder limit and overnight guards are speed bumps, not a real sandbox.** A determined or mistaken command
  can still get outside them. Don't run unattended on a machine with irreplaceable data.
- **Remote access exposes your PC.** With the phone app on, the login token is the *only* thing protecting it —
  anyone with the token and a network path can drive your computer. Keep it on a private network like
  [Tailscale](https://tailscale.com), never share the token, and change it if it leaks.
- **Web content can try to trick it (prompt injection).** Pages and files it reads may contain text aimed at the AI
  ("ignore your instructions and..."). Golem treats web content as data and asks before fetching, but review what
  it does after it has browsed.
- **Uncensored brains.** Auto mode uses a minimally-filtered local model (Hermes 3) that will answer things a
  safety-tuned model won't. It runs on your hardware; you are responsible for what you ask and do with it.
- **Your notification topic is semi-public.** Anyone who knows your ntfy topic name can read notifications you send
  — keep it secret.
- **The apps are unsigned.** Windows SmartScreen will warn on first run ("unknown publisher"). Only run builds you
  trust — and that cuts both ways for anyone you share this with.
- **No warranty.** This is early, personal software. Bugs happen. Use it on a machine you can afford to have make
  mistakes, and keep backups.

If you remember one thing: **don't run it with auto-approve, remote access, or overnight on anything you can't
afford to lose — and keep backups.**

## What you need

- **Windows 10 or 11.** (The code is mostly cross-platform Python, but the installer, shortcuts and a few helpers
  are Windows-only.)
- **[Ollama](https://ollama.com)** — free, runs the local AI models. Install it once; Golem talks to it.
- **A GPU helps a lot.** An NVIDIA card with 8 GB+ of VRAM runs good models fast. No GPU? It still works on the
  CPU, just slowly, with a small model.
- **Disk space** for whichever model you pull (roughly 2–9 GB each).
- **Python is only needed if you run from source** — the installer bundles everything, so end users need no Python.
- Optional: an [Anthropic API key](https://console.anthropic.com) if you want to use Claude as the brain instead
  of a local model (this one costs money per use and sends your prompts to Anthropic).

## Quick start

**The easy way (installed):**

1. Install **Ollama** from [ollama.com](https://ollama.com) and let it start.
2. Run **Golem-setup.exe** and finish the installer. You get a **Golem Builder** shortcut on your desktop and in
   the Start Menu. (On first launch Windows SmartScreen may warn because the app is unsigned — click
   **More info → Run anyway**.)
3. Open **Golem Builder**. It detects your GPU and recommends a model; click **Pull** to download it, tick the
   abilities you want, give it a name and personality, then **Save**.
4. Click **Save & Launch agent** — your agent opens in its own chat window. Talk to it.

**From source (developers):** see [Running and building from source](#running-and-building-from-source).

## Keeping Golem up to date

You don't have to reinstall for every new version. When you open the **Builder**, it quietly checks GitHub for a
newer release (this is a read-only check — turn it off on the **Add-ons** tab if you'd rather it didn't). If one
exists, a bar appears at the top: click **What's new** to read the notes, or **Update now** to download the new
installer and run it. The installer upgrades your copy in place — same folder, no admin, no uninstall first.

Golem will **never download or run an update on its own** — the check is automatic, but the download-and-install
only happens when *you* click the button. (A program that silently fetched and ran code from the internet would be
exactly the thing Golem is trying not to be.) You can also check any time with **Add-ons → Check for updates now**.

## The three apps

| App | File / exe | What it's for |
|---|---|---|
| **Golem Builder** | `Golem-Builder.exe` / `builder.py` | Set the agent up with checkboxes: pick the brain, toggle features, choose folders, write a personality, turn on phone access, install add-ons. Saves `agent_config.json`. |
| **Golem** (agent window) | `Golem.exe` / `agent_gui.py` | A desktop chat window you talk to the agent in. Launches the core and shows the conversation as chat bubbles, with Allow / Deny buttons for permissions. |
| **core** | `golem-core.exe` / `agent.py` | The agent engine itself — the loop, the tools, the model calls. The window runs this; you can also run it directly from the command line. |

## How it works

1. You type a task.
2. The program sends it to the model (local via Ollama, or Claude) together with a list of tools.
3. The model answers with text and/or tool calls (e.g. `run_shell`, `edit_file`).
4. The program runs each tool. It asks you first for anything that changes your machine, then sends the results
   back to the model.
5. Steps 3–4 repeat until the model stops calling tools.

That loop is the whole trick behind "taking over your PC". The model only ever *asks*; the program decides what
actually runs — which is why permission prompts (and the folder limit) are your real safety controls. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the technical details.

## The Builder

The Builder puts the whole agent behind checkboxes, so you never need the command line. Its tabs:

- **Brain** — detect your GPU and apply a recommended model, choose **Auto** (switches brains per task), one local
  model, or Claude; and pull any model: browse the **Ollama library** and **Hugging Face**, or pull a GGUF
  directly with `ollama pull hf.co/USER/REPO`.
- **Features** — tick the abilities the agent should have (each adds only its own tools). All optional.
- **Folders** — restrict where the agent may create, edit, move or delete files (see below).
- **Personality** — a name and a short description of how it should talk; presets included.
- **Phone** — turn on phone access and set the login token.
- **Add-ons** — install the image/sound pack, or enable the REA reverse-engineering tools.

**Save** writes `agent_config.json`; **Save & Launch agent** opens the chat window; **Save & Launch phone server**
starts the web app. The Builder and the command line are interchangeable — the Builder just writes the config, and
the agent runs it with `agent.py --config agent_config.json`.

## Tools the agent has

Each feature in the Builder switches on a set of these tools. "Asks permission" means it stops for your yes/no in
normal chat (auto-approve modes skip that — see Safety).

| Tool | Asks permission | What it does |
|---|---|---|
| `run_shell` | yes | PowerShell (Windows) / bash command |
| `start_program`, `program_output`, `stop_program` | start: yes | Run a game/app in the background, read its output, close it |
| `read_file`, `find_files`, `grep` | no | Read and search files |
| `write_file`, `edit_file` | yes | Create or change files |
| `web_search` | no | Free DuckDuckGo search: titles, URLs, snippets |
| `fetch_url` | yes | Download a page and read its text and links |
| `scan_network` | yes | List devices on your own LAN (IPs, names, open ports). Read-only discovery |
| `screenshot` | no | See the screen (screen control on) |
| `click`, `type_text`, `press_keys` | yes | Control mouse/keyboard (screen control on) |
| `godot_test` | no | Import + run a Godot project headless and report errors with file:line |
| `generate_image`, `generate_audio`, `generate_speech` | yes | Make images / music / speech locally (images-and-sound add-on) |
| `send_notification` | yes | Push a notification to your phone (ntfy) |

At a permission prompt in the terminal, answer `a` to allow that tool for the rest of the session. While computer
control is on, move the mouse into a screen corner to abort.

## Choosing a model

The Builder's **Brain** tab detects your GPU and recommends a model that fits its VRAM, applied with one click:

| VRAM | Typical recommendation |
|---|---|
| 24 GB+ | Auto: a strong text model + a vision model |
| 12–23 GB | Auto: Hermes 3 (text) + Gemma (vision) |
| 8–11 GB | one 8B model with tools |
| under 8 GB / no GPU | a small 3B model (CPU-friendly, slower) |

- Any Ollama model with **tools** support works. For screen control the model also needs **vision**.
- Bigger models follow multi-step tasks better; models larger than your VRAM spill to the CPU and crawl — check
  `ollama ps` for `100% GPU`.
- **Auto mode** picks per prompt: a fast, uncensored text model for normal work, and a vision model automatically
  when the task needs to see the screen or an image. Set the two with the `TEXT_MODEL` / `VISION_MODEL` env vars.
- Local models are weaker than Claude at long, multi-step tasks: expect the odd wrong tool argument, and re-run or
  rephrase when that happens.

## Personality

Give the agent a name and a voice in the Builder's Personality tab (or edit `personality.txt`). It's plain text
loaded into the system prompt. It sets how the agent *talks*, not its judgement — it still tests its work, asks
permission, and refuses unsafe things. A small local model follows a short, light personality better than a long
one.

## Appearance

Make the agent window feel like yours. Click the **🎨** button in the agent window's header to open the
**Appearance** panel:

- **Theme presets** — Midnight (default), Light, Terminal, Solarized, Rose — one click.
- **Colour pickers** for the background, panels, text, your message bubbles, the agent's bubbles, and the
  accent.
- **Font** family (any font on your PC) and size.
- **A custom background image** — choose any image and it sits behind the chat, with a *Darken* slider so
  your messages stay readable.

Every change previews live. **Save** keeps it (stored in the config under `appearance`); **Reset to
default** puts it back. It's purely look-and-feel — nothing about what the agent does changes.

## Voice

Golem can **read its replies out loud**, and it speaks **through the orb** — the orb pulses with light in
time with the speech (a flare on each loud syllable; no mouth, just light). Voice uses a small
**self-contained engine — no Python, no account, all local** — so it works on a fresh PC:

- On the Builder's **Add-ons → Voice** section, pick a voice and click **Install** (or **Install & test**
  to hear it). It's a one-time ~80 MB download — the Piper engine plus your chosen voice. Browse and
  preview more at the [Piper samples page](https://rhasspy.github.io/piper-samples/) and type any voice name.
- In the agent window, click the **🔊** button in the header to toggle speaking on or off.
- Keep the ambient orb running (it's on by default) to see Golem speak in pulses of light.

It all runs locally — nothing is sent anywhere. Only use voices you're allowed to; **cloning a voice from
your own recording is a planned add-on.**

## The ambient orb

Golem's ambient presence: a floating **ball of data** — a glowing core with 1s and 0s orbiting it in
3D — that sits on your desktop, always on top and **click-through** (your clicks pass right through it, so
it never gets in the way). It **breathes** gently, **pulses when Golem is responding**, and **flares with
light in time with the voice** when Golem speaks — so the orb looks like it's talking.

Launch it from the Builder's **Add-ons → Ambient orb → Launch orb** (or tick *Start it with the agent*),
or run `Golem-Orb.exe`. Control it from its **tray icon**:

- **Appearance…** — themes (**Twilight** default, plus Matrix, Ember, Ice, Void, Rose, **Ultron**), colour
  pickers for the core, glow, digits and sparks, and sliders for size, data density, speed and opacity.
  Changes preview live and save under `orb` in the config.
- **Unlock to move** to drag it (it remembers where), **Hide/Show**, and **Quit**.

It's built to be light — a small window and a cheap per-frame draw — so it runs smoothly even on modest
hardware.

## Limiting it to folders

In the **Folders** tab, list the folders the agent is allowed to change. When the list is non-empty, the agent may
only **create, edit, move or delete** inside those folders — in every mode, including over the phone. It can still
*read* files elsewhere. Leave the list empty to let it work anywhere (in normal chat it still asks before each
change). Remember this is a speed bump, not a hard sandbox (see Safety).

## Profiles

The **Profile** tab sets Golem up for a job in one click. A profile fills in the other tabs — which
abilities are on, sensible folder/protection defaults, a matching voice — and layers in its own
**guardrails** that hold no matter what personality you pick. Pick one, review the tabs, Save.

- **General** — everything on, no opinionated guardrails. The default, same as Golem has always been.
- **Cybersecurity** — CTF, authorized pentest, malware/RE and forensics. Turns on shell, files, web and
  local-network scan (pairs with the REA add-on), sandboxed by default, with a methodical "operate only on
  what you're authorized to" personality.
- **Shopping** — finds the best price across retailers with `compare_prices`, and helps you order — but
  **never buys on its own**: it never enters card details and only places an order after you confirm the
  exact item and total.
- **Medical** — for clinicians: documentation, literature and admin. Local-first so patient data stays on
  your machine; it's a documentation aid, **not** a diagnostic device, and it won't send identifiable data
  out without your say-so.
- **Teacher** — lesson planning, materials, rubrics and feedback. FERPA-minded with student data, and it
  drafts parent/admin messages but never sends them.
- **Student** — a tutor that explains, hints and quizzes. It teaches the method; it won't just do your
  homework for you to hand in.
- **Farmer** — field and crop planning, records, weather and market lookups. Local-first (works offline
  except for lookups), and it defers to product labels, a vet or an agronomist on anything regulated.

Profiles are just presets — you can change anything afterward, and the guardrails are additive on top of
Golem's usual safety (permission prompts, folder limits, prompt-injection re-confirm).

## Hardened Mode (for cautious users)

If running an AI agent on your PC makes you nervous, tick **Hardened Mode** on the Folders tab (or pass
`--hardened` on the command line). It's **off by default** and turns on every safety layer at once, so you don't
have to find them one by one:

- The agent can only **read *and* change** files inside your allowed folders — not just write to them. (Set some
  folders first, or there's nothing to confine it to — the Builder reminds you.) This also covers **shell
  commands**: `run_shell` is kept inside your folders and off secret paths too, so it can't be a side door around
  the file tools.
- It stays **extra-careful for the whole session** once it has read anything from the web or another program: it
  re-asks before each change even if you'd said "always allow", because that outside content could be trying to
  steer it (prompt injection). **Over the phone** (where nothing can prompt you), changes are instead *held* until
  your next message once untrusted content has been read that turn.
- **Phone access binds to this PC only** (loopback). If you still want your phone to reach it, pass your Tailscale
  address, e.g. `--host 100.x.y.z`.

Leave it off to run wide-open — nothing changes, and normal chat still asks before every change. Hardened Mode is
a strong set of in-process guardrails; for an OS-enforced wall, turn on **Sandbox Mode** below.

## Sandbox Mode (a real wall)

Hardened Mode's confinement is enforced by Golem's own code, which runs *before* the shell — a determined
command can still find ways around it (that's why the honest line has always been "speed bumps, not a
sandbox"). **Sandbox Mode** is the real wall: tick it on the Folders tab (under Hardened), or pass
`--sandbox`. It runs the agent's shell commands inside an **OS-enforced box where only your allowed folders
exist** — so even if a command breaks out of the agent, it simply can't see the rest of your disk. Your
`.ssh` keys, your `.env` files, your browser profile aren't "please don't"; they're *not there*.

- It's **off by default** and needs a backend. In this build that's **Windows Sandbox** (turn on the
  optional Windows feature: search *"Turn Windows features on or off"* → Windows Sandbox; needs Windows
  Pro/Enterprise). If no backend is present, the toggle is greyed out and Golem stays on in-process Hardened
  confinement — **it never pretends a wall exists when it doesn't.**
- Ticking it also turns on **Hardened Mode** — the sandbox sits *on top of* the in-process guards, never
  instead of them.
- A **network** dropdown controls whether the box can reach the internet (Off by default).
- `start_program` (a game or server with a window) still runs on the host, since it needs your real display;
  it stays subject to the Hardened shell guard.

A WSL backend is planned as a fallback for machines without Windows Sandbox.

## Using it from your phone

Turn on phone access in the Builder (or run `agent.py --config agent_config.json --serve`). It runs as a small web
app and prints the address to open and a one-time **login token**. On your phone, open the address, paste the
token once, and chat — each task runs on your PC and the reply comes back.

**Reach it from anywhere with [Tailscale](https://tailscale.com):** install Tailscale on the PC and the phone and
sign both into the same account — that puts them on one private network with nothing exposed to the public
internet. Then use the `100.x` address it prints.

Because nobody is at the keyboard to answer prompts, serving **auto-approves** tool calls, with the overnight
guards still on (system commands and out-of-folder deletes blocked). The token is the only thing guarding it — keep
it off untrusted networks. One task runs at a time, and the PC is kept awake while serving.

## Phone notifications

Golem can push a notification to your phone — handy for "overnight build finished" or "I'm stuck and need you". It
uses [ntfy](https://ntfy.sh): free, open-source, no account.

1. Install the **ntfy** app (iOS / Android) and subscribe to the secret topic in `ntfy_topic.txt` (a random one is
   generated for you). Anyone who knows the topic can read it, so keep the name secret.
2. That's it — the agent notifies you on its own when a long job finishes or it's stuck, and you can tell it to
   "text me when you're done". To self-host, set the `NTFY_SERVER` environment variable.

## Running on a schedule

Have the agent run a task at the same time every day. It registers a Windows Task Scheduler job (so it still fires
after a reboot), runs unattended, and pings your phone when done.

```
REM every night at 2am, work on the game for 4 hours in that folder
python agent.py --add-job game --at 02:00 --local gemma4:12b --overnight C:\Projects\space-shooter --hours 4 "work through the next milestone in GAME.md"

python agent.py --list-jobs            REM see what's scheduled
python agent.py --remove-job game      REM cancel one
python agent.py --run-job game         REM run it right now to test
```

The PC must be on and logged in at that time (it can be locked). Each job gets a launcher in `jobs\` you can
double-click to test, and output lands in `logs\`.

## Long unattended runs

For big jobs (build a game overnight, or over days), confine the agent to one folder and let it work without
prompts:

```
python agent.py --local gemma4:12b --overnight C:\Projects\my-game --hours 168 "Build a 2D platformer in pygame with 3 levels, a menu and sound."
```

- **Stays in one folder** — file tools refuse paths outside it; out-of-folder deletes and system commands are
  blocked (a speed bump, not a sandbox — keep backups).
- **No permission prompts**, and no mouse/keyboard control (screenshots still work).
- **Tests its own work**, keeps notes in `PROGRESS.md`, and resumes the same command if it's interrupted.
- **Keeps the PC awake**, stops at `TASK COMPLETE`, the `--hours` limit, or after it stalls. Everything is logged
  to `logs\`.

## Add-on: images and sound

Install from the Builder's **Add-ons** tab (or set up `gen/` by hand). Runs small models on your GPU, in their own
process so their heavy libraries stay out of the agent:

| Tool | Makes | Model |
|---|---|---|
| `generate_image` | a PNG; `transparent=true` cuts out the background for a sprite | Stable Diffusion 1.5 (~2 GB) |
| `generate_audio` | a `.wav` of music or a sound effect | MusicGen-small (~1.2 GB) |
| `generate_speech` | a `.wav` of spoken text | Piper (tiny) |

Needs Python 3.13 and a CUDA build of PyTorch; the Add-ons installer handles it, and the first use of each tool
downloads its model.

## Add-on: reverse engineering (REA)

Enable in the **Add-ons** tab. Adds the [REA](https://www.npmjs.com/package/rea-agents) toolset (analyse binaries,
apps, firmware, web pages) as an MCP server, run through `npx`, so it needs Node.js. Golem uses it only for
discovery and analysis of files and devices **you own** — it will not break into anything over the network.

## Godot game-making

The agent finds Godot automatically (Steam copy, PATH, Downloads, Program Files; or set the `GODOT` env var). It's
told to write Godot 4 GDScript, run `godot_test` after every change and fix the errors, write `tests/test_*.gd`
scripts, and open the game to screenshot it (needs screen control). Open the finished project in the Godot editor
as usual.

## The config file

The Builder reads and writes `agent_config.json` next to the agent. You can also hand-edit it:

```json
{
  "name": "Golem",
  "profile": "general",          // one-click preset: general | cybersecurity | shopping | medical |
                                 //   teacher | student | farmer (fills the tabs + adds guardrails)
  "model": {
    "mode": "auto",              // "auto" | "local" | "claude"
    "local_model": "gemma4:12b", // used when mode = "local"
    "text_model": "hermes3",     // auto mode: the work brain
    "vision_model": "gemma4:12b",// auto mode: the sees-the-screen brain
    "ctx": 65536
  },
  "features": {                  // every ability is opt-in
    "shell": true, "files": true, "web": true, "shopping": true, "computer": true,
    "network_scan": false, "godot": true, "image_gen": true,
    "audio_gen": true, "speech": true, "notifications": true
  },
  "serve": { "enabled": false, "host": "0.0.0.0", "port": 8765, "token": "" },
  "folders": [],                 // if non-empty, writes/edits/deletes stay inside these
  "sticky_taint": false,         // stay extra-careful all session after reading untrusted content
  "hardened": false,             // Hardened Mode: confine reads + shell too, sticky taint, loopback-only serve
  "sandbox": {                   // Sandbox Mode: run shell work in an OS box (needs Windows Sandbox); implies hardened
    "enabled": false, "backend": "auto", "network": "off"   // network: off | loopback | on
  },
  "check_updates": true,         // check GitHub for a newer release when the Builder opens
  "ollama_install_dir": "",      // optional: install Ollama off C: (passed to its installer's /DIR)
  "ollama_models_dir": "",       // optional: keep the big model files off C: (sets OLLAMA_MODELS)
  "personality": ""              // written to personality.txt on launch
}
```

## Running and building from source

```
git clone <this repo> && cd Golem
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt     # the agent + the Builder (PySide6)
python builder.py                   # open the Builder
```

Run the agent directly without the GUI:

```
python agent.py                              # interactive chat (needs ANTHROPIC_API_KEY, or use --local)
python agent.py --local gemma4:12b --computer
python agent.py --config agent_config.json --chat
python agent.py "summarize this folder"      # one task, then exit
```

Build the three exes and the installer (Windows):

```
pip install pyinstaller
pyinstaller --noconfirm --onefile --console   --collect-all anthropic --icon icon.ico --name golem-core    agent.py
pyinstaller --noconfirm --onefile --windowed                      --icon icon.ico --name Golem          agent_gui.py
pyinstaller --noconfirm --onefile --windowed                      --icon icon.ico --name Golem-Builder   builder.py
"C:\Path\to\ISCC.exe" golem.iss              # Inno Setup -> installer\Golem-setup.exe
```

Regenerate the app icon with `python make_icon.py`.

## Project layout

```
agent.py          the agent engine: the loop, tools, model calls, config, scheduling, phone server
agent_gui.py      the desktop chat window (Golem.exe)
builder.py        the setup GUI (Golem-Builder.exe)
gen/              image/audio/speech generators, run in their own venv
golem.iss         Inno Setup script that builds the installer
icon.ico          the app icon  (make_icon.py regenerates it)
requirements.txt  Python dependencies for the agent + Builder
mcp_servers.json  optional: MCP servers to load (e.g. REA)
personality.txt   the agent's voice (written by the Builder)
agent_config.json your saved setup (git-ignored; holds your phone token)
logs/             run logs
```

## Troubleshooting & FAQ

- **"Windows protected your PC" / unknown publisher.** Expected — the apps are unsigned. Click **More info → Run
  anyway**. It stops asking after the first time.
- **"Ollama not found" / the agent can't reach a model.** Install [Ollama](https://ollama.com) and make sure it's
  running. If it runs elsewhere, set `OLLAMA_HOST`.
- **It's really slow.** The model is probably bigger than your VRAM and spilling to the CPU. Pick the Builder's
  recommended model, or a smaller one; check `ollama ps` shows `100% GPU`.
- **No NVIDIA GPU.** It still works with a small (3B) model, just slower on the CPU.
- **It picks wrong tools or goes off-target.** Local models are weaker than Claude at long tasks — rephrase, retry,
  use a bigger model, or switch the brain to Claude.
- **Where are the logs?** In `logs\` next to the app. Read the newest file.
- **Is my data private?** Yes by default — the model runs locally. Exceptions: `web_search` / `fetch_url` reach the
  internet, Claude API mode sends prompts to Anthropic, and phone notifications go through the ntfy server.
- **How do I limit what it can touch?** Use the Folders tab (and don't use auto-approve/serve/overnight for
  anything sensitive).
- **Start over.** Delete `agent_config.json` — the Builder recreates defaults.

## License

[MIT](LICENSE) — free to use, modify and share. No warranty; see the Safety section and the license text.
