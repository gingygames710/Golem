# Getting started with Golem

**Never used an AI assistant before? This is for you.** Golem is a helper that lives on your own
computer. You type to it like texting a friend, and it can look things up on the web, compare prices,
and write and organize files for you. It's free, and your conversations stay on your PC.

You do **not** need to know anything technical. Follow these steps in order.

---

## The short version (3 steps)

1. **Install Golem** — download `Golem-setup.exe` from the [Releases page](https://github.com/gingygames710/Golem/releases/latest) and run it.
2. **Open "Golem Builder"** from your Start menu.
3. **Click the blue ⚡ Easy Mode button.** That's it — it sets everything up and opens Golem for you.

The first time, it will download the free "brain" it needs (a one-time download of a few GB — grab a
coffee). After that, Golem opens in seconds. The rest of this page just explains each step in more detail.

---

## Step 1 — Install Golem

1. Go to the [Releases page](https://github.com/gingygames710/Golem/releases/latest).
2. Under **Assets**, click **`Golem-setup.exe`** to download it.
3. Open the downloaded file.
4. Windows may show a blue **"Windows protected your PC"** box. This is normal for new software that
   isn't from a big company. Click **More info → Run anyway**. (Golem is open-source — you can read every
   line of what it does on this page.)
5. Click through the installer. It doesn't need administrator permission and installs just for you.

## Step 2 — Open Golem Builder

Find **Golem Builder** in your Start menu and open it. This is the setup screen. It looks like it has a
lot of options — **you can ignore all of them.** The only thing you need is the big blue button at the top.

## Step 3 — Click Easy Mode

Click **⚡ Easy Mode**. Here's what it does for you automatically:

- Sets Golem up as a **friendly, safe assistant** — it can search the web, compare prices, and read and
  write files in its own folder (`Documents\Golem`). It **cannot** run system commands or control your
  mouse and screen, so it can't mess anything up.
- Picks the right free **brain** for your computer.
- If the brain isn't installed yet, it **downloads and opens the installer** for you. Click **Next**
  through that one (it's called "Ollama" — it's the free engine Golem thinks with), then click
  **Easy Mode** again to finish.
- When everything's ready, **Golem opens in its own window** and you can start chatting.

That's the whole setup.

---

## What can I say to it?

Just talk to it normally. For example:

- *"What's a good beginner recipe for chicken thighs?"*
- *"Find me the cheapest 1TB external hard drive."*
- *"Make a packing list for a 5-day beach trip and save it as a text file."*
- *"Explain what a VPN is, simply."*

If you ask for something it isn't allowed to do in Easy Mode, it will tell you kindly and suggest a safe
way instead. It always asks before it changes a file.

## Want more power later?

Easy Mode is deliberately safe and simple. When you're comfortable, open **Golem Builder** again and
explore the **Profile** tab — there are ready-made setups for shopping, students, teachers, security work
and more — or turn on extra abilities yourself. Nothing you do there can be triggered by accident; Golem
always asks before doing anything that changes your computer.

## If something goes wrong

- **"It says the brain isn't set up."** Open Golem Builder → **Brain** tab → **Install for me**, click
  through the Ollama installer, then press **Easy Mode** again.
- **The blue Windows warning won't let me run it.** Click the small **More info** text, then the
  **Run anyway** button that appears.
- **Golem is slow to reply.** The first reply after opening can take a moment while the brain loads into
  memory; it speeds up after that. A newer graphics card (GPU) makes it much faster.
- **Running out of space on your C: drive?** The AI models are big (tens of GB) and normally land on C:.
  To put them on another drive: open Golem Builder → **Brain** tab → under *"Where Ollama installs &
  stores models"* click **Choose...** next to "Keep models in", pick a folder on your other drive, click
  **Apply models folder**, then restart Ollama (quit it from the system tray and reopen it).
- **Still stuck?** Open an issue on the [GitHub page](https://github.com/gingygames710/Golem/issues) and
  describe what happened.

---

*Golem runs on your computer and can change files in its folder. In Easy Mode it's fenced in and asks
before changing anything. It's early software with no warranty — but it's built to be careful.*
