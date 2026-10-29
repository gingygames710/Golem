# Mini Claude Code agent

A small agent in the style of Claude Code, written from scratch. Claude (`claude-opus-5-5`, via the API) is the brain;
`agent.py` is the harness that lets it act on your computer.

## How it works

1. You type a task.
2. The program sends it to Claude together with a list of tools.
3. Claude answers with text and/or tool calls (e.g. `run_shell`, `edit_file`).
4. The program runs each tool. It asks you first for anything that changes your machine, then sends the results back.
5. Steps 3-4 repeat until Claude stops calling tools.

That loop is the whole trick behind "taking over your PC". The model only ever *asks*; your code decides what actually runs.

## The Builder (no commands needed)

Don't want to remember flags? Run the **Builder**, a desktop app that puts the whole agent behind checkboxes:
pick a brain (and pull a model through Ollama right there), tick the abilities you want, give it a name and a
personality, turn on phone access, then **Save & Launch**.

```
pip install PySide6
python builder.py
```

It reads and writes `agent_config.json` next to `agent.py`, and the agent runs that config with
`python agent.py --config agent_config.json`. So the Builder and the command line are interchangeable - build it
in the GUI, or hand-edit the JSON, whichever you like. Every feature is optional: you can make anything from a bare
chat up to a fully loaded agent.

The Builder also lets you **limit the agent to chosen folders** (the Folders tab - it can read anywhere but may
only create, edit, move or delete inside those folders, even over the phone), and **install optional add-ons**
(the Add-ons tab - the image/sound generation pack, and the REA reverse-engineering tools). From the Brain tab
you can browse the full **Ollama library** and **Hugging Face** and pull any model, including any GGUF from Hugging
Face via `ollama pull hf.co/USER/REPO`.

**Planned:** detect the GPU in the machine and automatically recommend (or pick) the best-fitting model for its VRAM.

## Tools

| Tool | Asks permission | What it does |
|---|---|---|
| `run_shell` | yes | PowerShell (Windows) / bash command |
| `start_program`, `program_output`, `stop_program` | start: yes | Run a game/app in the background, read its output, close it |
| `godot_test` | no | Import + run a Godot project (or a test script) headless and report errors with file:line |
| `read_file`, `find_files`, `grep` | no | Read and search files |
| `write_file`, `edit_file` | yes | Create or change files |
| `scan_network` | yes | List devices on your own LAN (IPs, names, open ports). Read-only discovery |
| `web_search` | no | Free DuckDuckGo search: titles, URLs, snippets |
| `fetch_url` | yes | Download a page and read its text and links |
| `screenshot` | no | See the screen (`--computer` only) |
| `click`, `type_text`, `press_keys` | yes | Control mouse/keyboard (`--computer` only) |

Web pages can contain text written to trick an AI ("ignore your instructions and run..."). The agent is told to treat
web content as information only, and `fetch_url` asks first so a page can't quietly send it to other sites. Still,
read what it wants to run before approving anything after it has browsed. Search snippets can be out of date: ask it
to open the page (`fetch_url`) when the exact answer matters. With `--computer`, it can also use your real browser by
clicking and typing.

At a permission prompt, answer `a` to allow that tool for the rest of the session. While computer control is on,
move the mouse into a screen corner to abort.

## Setup

```
pip install -r requirements.txt
set ANTHROPIC_API_KEY=sk-ant-...     (PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-...")
```

Get an API key at https://console.anthropic.com. Usage is billed per token.

## Run

```
python agent.py                         # interactive chat
python agent.py "summarize this folder" # one task, then exit
python agent.py --computer              # also enable screen/mouse/keyboard
python agent.py --yes                   # skip permission prompts (risky)
```

## Personality

Edit `personality.txt` to change the agent's name, voice and attitude, then restart it. It's plain text, loaded
into the system prompt. It sets how the agent *talks*, not its judgement — it still tests its work, asks permission,
and refuses unsafe things. A small local model follows a light, short personality better than a long, elaborate one.

## Free local mode (no API key, no tokens billed)

Runs the same loop against a model on your own GPU through [Ollama](https://ollama.com):

```
python agent.py --local gemma4:12b --computer
```

Recommended on a 16 GB GPU: `gemma4:12b`. It has vision + tools, is about 8 GB, runs fully on the GPU, and measured
~55 tokens/sec. Models bigger than your GPU memory spill onto the CPU and crawl: `qwen3.8:27b` (18 GB loaded) ran 26% on the CPU
at ~2.6 tokens/sec. Check with `ollama ps`: you want `100% GPU`.

- Any Ollama model with **tools** support works. Bigger models follow multi-step tasks better.
- For `--computer`, the model also needs **vision**, or it can't read the screenshots (`qwen3` is text-only).
- Local models are much weaker than Claude at long, multi-step tasks: expect occasional wrong tool arguments
  or off-target replies, and re-run or rephrase when that happens.
- Set `OLLAMA_HOST` if Ollama runs somewhere other than `http://localhost:11434`.
- Local models get a 64K-token context by default (`--ctx` to change; Ollama's own default is only 4K).

### Auto brain-switching

Don't want to pick a model? Run `--local auto` and the agent chooses per prompt: a fast, **uncensored** text
model (Hermes 3) for normal work, and a **vision** model (Gemma) automatically whenever the task needs to see
the screen or a generated image. It tells you which brain it used each turn.

```
python agent.py --local auto --computer
```

- Set the two models with `--text-model` / `--vision-model` (or the `TEXT_MODEL` / `VISION_MODEL` env vars).
- Both stay loaded in VRAM once used (8 GB + 5 GB fits your 16 GB), so switching is instant.
- Vision is only chosen when `--computer` is on - otherwise there's no screen to look at, so everything uses
  the fast uncensored model.
- About "uncensored": fully-stripped models (e.g. Dolphin) usually lose tool-calling and break the agent -
  Hermes 3 stays minimally restricted *and* keeps tools working. Any model whose `ollama show` lists `tools`
  works as the text model; it also needs `vision` to be the vision model.

## Reviving an old device

The agent can help bring an aging machine or gadget back to life along legitimate paths:

- **`scan_network`** shows what's on your own network, so you can identify a device and its open services.
- **REA** (via `mcp_call`) reverse-engineers firmware or app files **you already have** — downloaded from the
  maker, or pulled off a device you own — so you can understand how it works (e.g. where it loads ads).
- Use a device's **official** channels: its update tool, a documented debug mode (ADB on Android TVs), a USB
  reflash, or a custom-firmware project for that exact model.
- For ads on a smart TV, blocking the ad servers at the network level (e.g. Pi-hole) is usually simpler and
  safer than reflashing.

The agent will not break into devices over the network. Discovery and analysis only — the access has to come
through a door the device legitimately offers.

## Godot

The agent finds Godot automatically (your Steam copy, PATH, Downloads, Program Files) and tells the model where it is.
To point it at a different copy, set the `GODOT` environment variable to the `.exe`. It is told to:

- write Godot 4 GDScript (not Godot 3), and build most nodes in code rather than in hand-written `.tscn` files;
- run `godot_test` after every change and fix every error it reports;
- write `tests/test_*.gd` scripts (`extends SceneTree`, `assert()`, then `quit()`) for game logic;
- open the game in a window with `start_program` and take a screenshot to check how it looks (needs `--computer`).

```
python agent.py --local gemma4:12b --computer --overnight C:\Projects\my-godot-game --hours 12 "Make a Godot 4 top-down shooter with ..."
```

Open the finished project in the Godot editor as usual (Import, then pick its `project.godot`).

## Long unattended runs (overnight or for days)

```
python agent.py --local gemma4:12b --computer --overnight C:\Projects\my-game --hours 168 "Build a 2D platformer in pygame with 3 levels, a menu and sound."
```

- **Stays in one folder.** File tools refuse paths outside it; shell commands that delete/move things outside it, or
  system commands (shutdown, format, registry, firewall...), are blocked. This is a speed bump, not a sandbox: a
  determined command can still get around it, so don't run it on a PC with things you can't afford to lose
  without a backup.
- **No permission prompts**, and no mouse clicking or typing (screenshots still work with `--computer`).
- **Tests its own work:** writes and runs automated tests, and starts the game in the background to check output and
  take screenshots.
- **Keeps notes in `PROGRESS.md`** in the project folder. When its memory fills up it starts a fresh conversation from
  those notes. If the run stops (Ctrl+C, crash, Windows Update reboot), run the same command again and it resumes.
- **Keeps the PC awake** while running, and stops when it says `TASK COMPLETE`, when the `--hours` limit is reached,
  or after 3 rounds where it stopped doing anything.
- **Everything is logged** to `logs\` next to `agent.py`. Read the newest file in the morning.
- Plug in the laptop, and expect a small local model to need several runs and clear, specific instructions for big
  projects. Break a big app into stages ("first the menu and player movement") for the best results.

## Making images and sound (local, free)

The agent can generate game assets on your own GPU, with small models that fit alongside the local LLM:

| Tool | Makes | Model |
|---|---|---|
| `generate_image` | a PNG; `transparent=true` cuts out the background for a sprite (RGBA) | Stable Diffusion 1.5 (~2 GB) |
| `generate_audio` | a `.wav` of music or a sound effect | MusicGen-small (~1.2 GB) |
| `generate_speech` | a `.wav` of spoken text | Piper (tiny) |

These run in their own process (in `gen/`), so their heavy libraries stay out of the agent and the GPU memory
is freed as soon as each one finishes. Install them once into a separate environment:

```
py -3.13 -m venv gen\.venv
gen\.venv\Scripts\pip install --index-url https://download.pytorch.org/whl/cu128 torch torchaudio
gen\.venv\Scripts\pip install -r gen\requirements.txt
```

Install `torch` **first**, with that `--index-url` line - it forces the CUDA 12.8 build, which is what an RTX
50-series (Blackwell) card needs. If torch comes from plain PyPI you get the CPU-only build and generation
crawls. Use Python 3.13 (torch has no 3.14 wheels yet); on an older GPU pick your CUDA version from
https://pytorch.org/get-started/locally/. The first use of each tool downloads its model
(a minute or so); after that it's quick. SD 1.5 is small and really meant for sprites and simple art - describe
things concretely ("a small sleek red spaceship seen from directly above") and regenerate from the preview the
tool hands back. For sharper results later, drop a bigger checkpoint (SDXL) into `gen_image.py`'s `--model`.

## Using it from your phone

Run the agent as a small web app and drive it from your phone's browser:

```
python agent.py --local gemma4:12b --serve
```

It prints the addresses to open and a one-time **login token** (set your own with `--token`). On the phone,
open the address, paste the token once, and type tasks - each one runs on your PC and the reply comes back in
the chat. It binds all network interfaces (`--host`/`--port` to change) but every request needs the token.

**Reach it from anywhere with [Tailscale](https://tailscale.com):** install Tailscale on both the PC and the
phone and sign both into the same account - that puts them on one private network with no ports forwarded and
nothing exposed to the public internet. Then use the `100.x` address the agent prints. Keep the web app off any
untrusted network: the token is the only thing guarding it.

Because nobody is at the keyboard to answer prompts, `--serve` **auto-approves** tool calls (like `--yes`), with
the overnight guards still on (system commands and deletes outside the folder are blocked). To confine it to one
project, add `--overnight C:\Projects\my-game` alongside `--serve`. One task runs at a time. While serving, the PC
is kept awake so it can't drop offline by going to sleep.

## Getting notified on your phone

The agent can push a notification to your phone - handy for "overnight build finished" or "I'm stuck and need
you" while you're away. It uses [ntfy](https://ntfy.sh): free, open-source, no account, and self-hostable later.

1. Install the **ntfy** app (iOS / Android) and subscribe to the secret topic in `ntfy_topic.txt` (a random one
   was generated for you). Anyone who knows the topic can read it, so keep the name secret.
2. That's it - the agent calls `send_notification` on its own when it finishes or gets stuck, and you can ask it
   to "text me when you're done". To self-host instead, set the `NTFY_SERVER` environment variable to your server.

## Doing things on a schedule

Have the agent run a task at the same time every day. It registers a Windows Task Scheduler job (so it still
fires after a reboot) that launches the agent unattended with a saved task, and pings your phone when it's done.

```
REM every night at 2am, work on the game for 4 hours in that folder
python agent.py --add-job game --at 02:00 --local gemma4:12b --overnight C:\Projects\space-shooter --hours 4 "work through the next milestone in GAME.md"

REM every morning at 7am, a quick one-off task (no folder = a single unattended run)
python agent.py --add-job brief --at 07:00 --local gemma4:12b "summarize what changed in C:\Projects\space-shooter yesterday and text me the highlights"

python agent.py --list-jobs            REM see what's scheduled
python agent.py --remove-job game      REM cancel one
python agent.py --run-job game         REM run it right now to test
```

A job with `--overnight FOLDER` runs confined to that folder with snapshots and the overnight guards (good for
long builds); without a folder it's a single unattended pass (good for a quick daily task). The PC must be on and
logged in at that time - it can be locked, but a logged-out machine won't run it (that would need storing your
password). Each job gets a launcher in `jobs\` you can double-click to test, and output lands in `logs\`.
