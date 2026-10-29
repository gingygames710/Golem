# Architecture

How Golem is put together, for anyone reading or extending the code. The whole thing is small, plain Python plus
a PySide6 GUI; there's no framework to learn.

## The core idea

An LLM can't *do* anything by itself — it only emits text. Golem is the **harness** around it: it hands the model
a task and a list of tools, runs the tool calls the model asks for, feeds the results back, and loops until the
model is done. The model proposes; the harness disposes. That boundary is where every safety control lives.

## The loop (`agent.py`)

`Agent.turn(user_input)` runs one exchange:

1. Append the user message; call the model.
   - **Claude path** — `turn()` uses the Anthropic SDK and streams the reply.
   - **Local path** — `turn_ollama()` calls Ollama's `/api/chat` with the tools as JSON function specs, streaming
     tokens.
2. The reply is text and/or `tool_use` blocks. For each tool call, `run_tool()` checks permission, runs the Python
   function, and returns a `tool_result`.
3. Results go back to the model; repeat until there are no more tool calls (or `max_steps`).

`run_overnight()` wraps `turn()` for long unattended jobs: it keeps `PROGRESS.md`, starts a fresh conversation
from those notes when context fills, snapshots the workspace with git, and stops on `TASK COMPLETE` / a time limit
/ stalling.

## Tools

Each tool is a plain function plus a schema built by `_tool(name, description, props, required, needs_permission)`.
They're registered in two dicts:

- `TOOLS` — the base set (shell, files, web, network, gen, notifications, …).
- `COMPUTER_TOOLS` — screen/mouse/keyboard, added only with screen control on.

`run_tool()` looks the function up, validates required args, enforces the guards below, then calls it. A tool
returns a string, or (for `screenshot` / image previews) a list of text + image blocks.

## Config (the Builder contract)

The Builder writes `agent_config.json`; `agent.py --config` reads it:

- `default_config()` is the canonical shape.
- `FEATURE_TOOLS` maps each Builder feature (e.g. `"web"`) to the tool names it enables. `enabled_tools_from()`
  turns the ticked features into a set of tool names.
- `apply_config(args, cfg)` fills the argparse namespace from the config (model, computer, serve, folders,
  personality) and returns that set. `Agent.__init__` subsets `TOOLS` by it, so unticked features simply aren't
  present.

## Model routing (auto mode)

With `mode: "auto"`, `pick_model(user_input)` chooses per turn: the **vision** model when screen control is on
*and* the prompt matches `VISION_HINTS` (screenshot, click, sprite, "what's on…", …), otherwise the fast
**text** model. Both stay resident in VRAM so switching is instant. When the active model isn't the vision one,
`ollama_request()` strips image blocks from the messages so a text model never receives images.

## Safety controls

- **Permission** — `ask_permission()` prompts unless the tool is in `always_allowed`. `--yes` / `--serve` /
  overnight pre-fill `always_allowed` (no prompts).
- **Unattended guard** — when nobody's watching (`unattended`), `run_shell` / `start_program` pass through
  `check_unattended_command()`: `BLOCKED_COMMANDS` (shutdown, format, registry, firewall, …) are refused, and
  `DELETE_VERBS` outside the allowed bounds are refused.
- **Folder limit** — `ALLOWED_DIRS` (from config `folders`) bounds `write_file` / `edit_file` via
  `_check_writable()`, and the delete guard above, in *every* mode. Reads are unrestricted. `WORKSPACE` is the
  single-folder confinement used by `--overnight`.
- These are speed bumps, not a kernel sandbox — documented as such in the README.

## The three executables

All three freeze with PyInstaller. Because a frozen `__file__` points into a temp dir, each resolves its home as
`Path(sys.executable).parent` when `sys.frozen` is set, so an installed copy finds its files next to the exe.

- **`golem-core.exe`** — `agent.py`, a console app (so its stdout can be captured).
- **`Golem.exe`** — `agent_gui.py`, the windowed chat UI. It launches the core with `subprocess` (stdin/stdout
  pipes, `CREATE_NO_WINDOW`, unbuffered), reads output in a `Reader` thread in *chunks* (not lines, so a
  no-newline permission prompt still shows), and renders it into chat bubbles; Allow/Deny write `y`/`n`/`a` to the
  core's stdin. It runs `golem-core.exe` if present, else `python agent.py`.
- **`Golem-Builder.exe`** — `builder.py`. Launches `Golem.exe` (or `agent_gui.py`) for "Launch agent".

## Other pieces

- **Phone server** — `run_server()` is a stdlib `ThreadingHTTPServer`, token-gated (`X-Token`,
  `secrets.compare_digest`), serving an inline mobile chat page; a lock runs one task at a time.
- **Scheduling** — `add_job()` writes `jobs/<name>.cmd` and registers a `schtasks` daily task that runs
  `--run-job`; jobs are saved in `schedule.json`.
- **Image/sound** — generators live in `gen/` with their own venv; `_run_gen()` shells out to that Python so the
  heavy ML libraries stay out of the agent process and free their VRAM on exit.
- **GPU recommendation** — `detect_gpu()` (in `builder.py`) reads `nvidia-smi`; `recommend_for(vram)` maps VRAM to
  a fitting, tool-capable model.

## Adding a tool

1. Write the function in `agent.py`.
2. Register it in `TOOLS` with `_tool(...)` (set `needs_permission` if it changes the machine).
3. Add its name to the right `FEATURE_TOOLS` group (or a new one).
4. If it's a new feature, add a row to `FEATURES` in `builder.py` so it gets a checkbox.
