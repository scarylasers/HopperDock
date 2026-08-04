<div align="center">

<img src="docs/bunny.png" alt="HopperDock" width="140">

# HopperDock

**A small floating toolbar for Windows that tiles your windows, remembers exactly
where they go, and launches the scripts you actually use.**

Built for VR streamers and artists working on a small screen.

[![License: MIT](https://img.shields.io/badge/License-MIT-ff2d95.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-00e5c0.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-a06bff.svg)

📖 **[Read the illustrated guide →](https://scarylasers.github.io/HopperDock/)**
&nbsp;•&nbsp;
⬇️ **[Download the .exe →](https://github.com/scarylasers/HopperDock/releases/latest)**

</div>

---

## Contents

- [Why it exists](#why-it-exists)
- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Install](#install)
- [First run](#first-run)
- [The dock, button by button](#the-dock-button-by-button)
- [Saved layouts](#saved-layouts)
- [Categories and shortcuts](#categories-and-shortcuts)
- [Pinned apps](#pinned-apps)
- [Pinning the dock to a screen edge](#pinning-the-dock-to-a-screen-edge)
- [The bunny menu](#the-bunny-menu-all-settings)
- [Where your settings live](#where-your-settings-live)
- [Building a standalone .exe](#building-a-standalone-exe)
- [Troubleshooting](#troubleshooting)

---

## Why it exists

I built this to stream VR.

Streaming from a headset means juggling a pile of windows you can't see while
you're in there — the headset mirror, OBS, chat, Voicemeeter, the game. Every
session started with the same five minutes of dragging things into place, and
the Windows taskbar was no help: you can't pin a script to it, can't group
anything, and can't fit what you need.

So the dock does the two jobs the taskbar won't: put my windows back where they
belong, and give me one click for the things I run constantly.

**What that looks like in practice:**

- **Oculus Mirror** — the window that puts your headset view on stream, buried in
  `Program Files\Oculus\Support\oculus-diagnostics\`. HopperDock finds it on
  install and puts it one click away, along with the **Debug Tool** for when
  you're fixing bitrate or encode lag mid-session.
- **Killing a game that didn't quit** — some Meta titles (Population One is the
  repeat offender) leave their process running after you quit inside the headset,
  still holding the headset, your mic and the GPU. A two-line `taskkill` script on
  the dock fixes it; that script ships in `examples/`.
- **Rebuilding the stream layout** — tiling keeps OBS, chat, the mirror and the
  audio mixer where you expect them, which matters when you're headset-blind and
  reaching for a window you can't see. Save it once, restore it every session.
- **Drawing away from the desk** — on a Wacom from the couch or a laptop, screen
  space is scarce. Tiling arranges reference, canvas and palette instead of
  stacking them, and pinning the dock means a maximized canvas stops beside it.

Honestly, **the launcher outgrew the window management.** The taskbar can't hold a
`.bat` file, can't group things, and runs out of room fast. The colour-coded
categories turned out to be the part I use most — it's the taskbar I wanted.

None of it is VR-specific; it's just as useful for editing, coding, or anything
else with a routine.

## What it does

- **Move every window to a monitor** in one click.
- **Tile windows** into columns or grids on any monitor.
- **Save and restore window layouts** — position, size, maximized *and*
  minimized state, across all monitors.
- **Launch shortcuts** grouped into colour-coded categories that slide out
  from the dock.
- **Pin apps** to the dock as icons, and show/hide them with a click.
- **Dock to a screen edge** as a real Windows AppBar, so maximized windows
  stop underneath it instead of behind it.
- **Find your VR tools automatically** — Oculus Mirror and the Debug Tool are
  added to the starter VR category if the runtime is installed, and quietly
  skipped if it isn't.
- Vertical or horizontal, dark or light, on any monitor.

---

## Requirements

| | |
|---|---|
| OS | Windows 10 or 11 |
| Python | 3.10+ (3.11 recommended) — **not needed** if you use the built `.exe` |

Python packages (see `requirements.txt`):

| Package | Used for | Required? |
|---|---|---|
| `Pillow` | icon and logo loading/scaling | yes |
| `pystray` | system tray icon | yes |
| `tkinterdnd2` | drag-and-drop files onto the dock | optional |

`tkinterdnd2` is optional — without it the dock runs fine, you just can't drag
files onto it to create shortcuts.

---

## Install

### Option A — run from source

```bat
git clone <your-repo-url> HopperDock
cd HopperDock
pip install -r requirements.txt
run_dock.bat
```

`run_dock.bat` starts the dock with no console window. If something goes wrong,
use `run_dock_visible.bat` instead — same program, but it keeps a console open
so you can see the error.

### Option B — the standalone .exe

Download `HopperDock.exe` from the [latest release](https://github.com/scarylasers/HopperDock/releases/latest)
and double-click it. No Python needed. Put it wherever you like; it stores its
settings in your user profile, not next to the exe.

**Windows will warn you the first time.** You'll get a blue
*"Windows protected your PC"* box. Click the small **More info** link, then the
**Run anyway** button that appears. You only do this once.

That warning shows up for any program Windows hasn't seen many people run yet.
Publishers avoid it by buying a code-signing certificate — a few hundred dollars
a year. HopperDock is free and made by one person, so it doesn't have one. The
warning is about a missing receipt, not about anything found in the app.

If you'd rather verify than trust:

| To… | Do this |
|---|---|
| Check the file is the real one | `Get-FileHash HopperDock.exe` and compare to the SHA-256 below |
| Get it scanned | Upload to [VirusTotal](https://www.virustotal.com/gui/home/upload) — free, ~70 engines |
| Skip the exe | Use Option A — it's one readable Python file |

```
SHA-256  HopperDock.exe  v1.5.2
716FC1D3A9539C9ACF0E8EC197197BFDC0C7E1B88EB5C74CCDF915FE82A61FF1
```

**To uninstall:** delete the `.exe`, then delete `%USERPROFILE%\WindowDock\`. If
you enabled **Start on Login**, turn it off from the bunny menu first — that's the
only thing written outside its own folder.

### Start it automatically

Right-click the bunny → **Start on Login**. That writes a single value to
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, and unticking it removes
that value again. Nothing else is touched.

---

## First run

The dock appears as a floating vertical strip with a starter set of categories
(VR, Audio, Apps, Scripts) already filled with example shortcuts that work on a
stock Windows install — Notepad, Task Manager, Windows sound settings, SteamVR,
and three example `.bat` files.

If the Oculus runtime is installed, **Oculus Mirror** and the **Debug Tool** are
added to the VR category too. If it isn't, they're skipped rather than left as
dead shortcuts.

Those `.bat` examples live in the `examples` folder next to the app. Open
`examples/README.txt` for a short "add your own scripts" walkthrough. They're
there so the app does something useful on day one, and so it's obvious where
your own scripts should go. Delete them once you've got your own.

**Drag the bunny** at the top to move the dock around.
**Right-click the bunny** for every setting.

---

## The dock, button by button

Reading top to bottom in vertical mode (left to right in horizontal mode):

| Button | Left-click | Right-click |
|---|---|---|
| 🐰 **Bunny** | drag to move the dock | the settings menu |
| **M1 / M2 / …** | move *every* window to that monitor | — |
| **1 2 3 4** | restore that saved layout | layout menu (save / rename / icon / clear) |
| **T** (or TILE) | tiling menu — 2 columns, 3 columns, 2×2, 3×2, per monitor | — |
| **Category buttons** | open that category's shortcut popout | category menu (rename / icon / colours / remove) |
| **Pinned app icons** | show/hide the app, or launch it if it isn't running | edit or remove that app |

There's one M button per detected monitor. If you plug in a new screen, use
**Refresh Monitors** in the bunny menu.

---

## Saved layouts

Four slots. A layout records every open window's position, size, and whether it
was maximized or minimized.

**To save:** right-click a slot → **Save Layout Here**. It snapshots everything
open right now.

**To restore:** left-click the slot. Windows are matched *by title*, so an app
has to be open (and showing roughly the same title) for it to be moved back.

**To name it:** right-click → **Rename…**. Type "Streaming" or "Editing" and the
button shows it instead of the number. The name is cosmetic — renaming never
disturbs the windows you saved.

**To give it an icon:** right-click → **Set Icon…** and pick a PNG or ICO. The
icon replaces the number entirely.

**Minimized windows** are saved and restored properly: a minimized window is
recorded with the position it would restore to, and comes back minimized to that
same spot. Restoring a layout won't drag a minimized window onto your screen.

> Layouts saved before v1.5 don't carry minimized state. Re-save the slot once
> to pick it up.

---

## Categories and shortcuts

Categories are the coloured buttons. Clicking one slides out a popout listing
its shortcuts; clicking a shortcut launches it. **Clicking the popout's coloured
header closes it again** — as does clicking the category button a second time.

### Adding a shortcut

1. Click a category button to open its popout.
2. Click the **✏ pencil** in the popout header to enter edit mode.
3. Click **+ Add Shortcut** and pick a file.
4. Click the **✓** to leave edit mode.

In edit mode each shortcut grows a **×** to delete it.

### Or drag things onto the dock

| Drop this | You get |
|---|---|
| A file or shortcut | A **pinned app icon** at the bottom of the dock — an `.exe`, a `.lnk` from your Start Menu, a script, anything |
| A folder | A whole **new category**, with every file inside it added as a shortcut |

Dropped apps arrive wearing **their own icon** — it's pulled straight out of the
program and cached in `%USERPROFILE%\WindowDock\icons\`. Microsoft Store apps
work too: their launcher is a stub with no icon in it, so the packaged artwork
is read instead.

Dropping the same file twice won't duplicate it.

> A shortcut whose target no longer exists (say, an app uninstalled or a
> shortcut left behind by an old Windows profile) has no icon for Windows to
> show, so it comes in with the generic document icon. That's a sign the
> shortcut itself is broken, not the dock.

### Customising a category

Right-click a category button:

| Option | What it does |
|---|---|
| **Rename** | change the category name |
| **Show Text / Show Icon** | display the full name or the short icon text |
| **Edit Icon Text** | the 1–6 character label (e.g. `VR`, `MIX`) |
| **Set Icon Image…** | use a PNG/ICO instead of text — this wins over the text icon |
| **Clear Icon Image** | go back to the text icon |
| **Button Colour / Font Colour** | pick from the theme palette |
| **Remove Category** | delete it and its shortcuts |

Add new categories from the bunny menu → **Categories → + Add Category**.

### What can be a shortcut

| You point it at | How it runs |
|---|---|
| `.py` | `pythonw yourfile.py` |
| `.ps1` | `powershell -ExecutionPolicy Bypass -File yourfile.ps1` |
| `.bat` / `.cmd` | run directly |
| anything else | handed to Windows |

That last row is the useful one — `.exe`, `.lnk`, a folder, or a URL all work.
So do URI schemes like `steam://run/250820` or `ms-settings:sound`, which is how
the starter shortcuts launch Windows settings pages.

**Microsoft Store apps** (Spotify, WhatsApp, etc.) have no normal `Program Files`
path. Point at the app-execution alias instead:

```
C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\Spotify.exe
```

---

## Pinned apps

The icons at the bottom. Unlike shortcuts, a pinned app **toggles**: if its
window is showing it gets hidden, if it's hidden it gets shown and pulled back
to your primary monitor, and if it isn't running it gets launched.

Add one from the bunny menu → **Pinned Apps → + Add Pinned App**. You'll be
asked for the file, a name, and optionally an icon image.

The **Window Title** field is how it finds an already-running window, and it has
to match the title *exactly*. For apps that rewrite their own title bar — media
players showing the current track, browsers showing the current tab — leave
Window Title blank. The dock will then simply launch/focus the app every time,
which is what you want for those.

---

## Pinning the dock to a screen edge

Bunny menu → **📌 Pin to Edge**.

This registers the dock as a Windows **AppBar**. The difference from just moving
it to the edge: Windows reserves that strip of screen, so maximized windows stop
at the dock instead of hiding behind it — exactly how the taskbar behaves.

- **Vertical** dock pins to the **right** edge, full height.
- **Horizontal** dock pins to the **top** edge, full width.

Pin to a different monitor by moving the dock there first (or bunny menu →
**Move Dock to Other Monitor**), then pinning. The dock re-pins to the same
monitor when you restart.

Switch orientation any time with **Switch to Horizontal / Vertical**; the pin
follows.

---

## The bunny menu (all settings)

Right-click the bunny:

| | |
|---|---|
| **Switch to Horizontal / Vertical** | rotate the dock |
| **📌 Pin to Edge / Unpin** | AppBar mode on/off |
| **🖥 Move Dock to Other Monitor** | cycle to the next monitor |
| **🔄 Refresh Monitors** | re-detect displays after plugging one in |
| **📌 Pinned Apps** | add / edit / remove |
| **📂 Categories** | add a category |
| **🎨 Themes** | Neon Bunny (dark) or Pastel Bunny (light) |
| **Start on Login** | add/remove the Run registry entry |
| **Tooltips** | turn hover tooltips on or off |
| **⚙ Config → Export / Import** | save or load everything as one JSON bundle |
| **✕ Quit HopperDock** | exit |

The dock also lives in your **system tray** — click the tray icon to show/hide
it, right-click for a short menu.

**Export/Import** bundles your settings, shortcuts, and layouts into a single
JSON file. Use it to move your setup to another machine, or as a backup before
experimenting. Note that it stores *paths*, so shortcuts only work on the new
machine if the same files exist there.

---

## Where your settings live

Everything is per-user, in:

```
%USERPROFILE%\WindowDock\
```

| File | Contents |
|---|---|
| `settings.json` | dock position, orientation, pinned apps, theme, layout names/icons |
| `shortcuts.json` | categories and their shortcuts |
| `layouts.json` | the four saved window layouts |
| `logs\window_dock.log` | diagnostic log |

Nothing is stored next to the app, so the program folder stays disposable —
delete it, reinstall, and your setup is still there.

**These files are treated as precious.** If one ever fails to parse, HopperDock
copies it aside as `<name>.corrupt-<timestamp>` before falling back to defaults,
so a bad byte can never silently erase your shortcuts. Writes are atomic
(temp file + replace), so an interrupted save can't truncate a config either.

To reset one section, close the dock and delete just that file.
To reset everything, delete the whole `WindowDock` folder.

---

## Building a standalone .exe

```bat
pip install pyinstaller
pyinstaller HopperDock.spec
```

The result lands in `dist\HopperDock.exe` as a single self-contained file.

The spec bundles the logo assets, the `examples` folder, and the tkdnd Tcl
extension that drag-and-drop needs. All its paths are relative, so the build
works from a fresh clone on any machine.

> One-file builds unpack to a temp folder that gets a **different name every
> launch**. That's why the starter `examples` scripts are copied out to
> `%USERPROFILE%\WindowDock\examples\` on first run — a shortcut pointing into
> the temp folder would break on the next start.

---

## Troubleshooting

**The dock doesn't start / vanishes immediately.**
Run `run_dock_visible.bat` to see the error, and check
`%USERPROFILE%\WindowDock\logs\window_dock.log`.

**No tray icon, no bunny logo.**
`Pillow` and/or `pystray` aren't installed: `pip install -r requirements.txt`.
The dock still runs without them — it falls back to a 🐰 emoji.

**A layout doesn't restore some windows.**
Windows are matched by title. If the app was closed, or its title changed
(a document name, a track name), it won't be found. Re-save the layout with
everything open the way you want it.

**"Pin to Edge" leaves a gap at the edge.**
Fixed in v1.5. If you're on an older build, restarting the dock clears the stale
AppBar reservation that caused it.

**Maximized windows still hide behind the dock.**
The dock is floating, not pinned. Bunny menu → **Pin to Edge**.

**A pinned app relaunches instead of toggling.**
Its Window Title doesn't match the real window title. Right-click the icon →
**Edit**, and either correct the title or clear it (see
[Pinned apps](#pinned-apps)).

**Dragging files onto the dock does nothing.**
`tkinterdnd2` isn't installed. `pip install tkinterdnd2`, then restart.

**Everything reset after I edited a config file by hand.**
It shouldn't have — look for `<name>.corrupt-<timestamp>` in
`%USERPROFILE%\WindowDock\`, which is your original file. If you edit these by
hand, save as **UTF-8 without a BOM**.

---

## Notes

- Moving windows across monitors simulates <kbd>Win</kbd>+<kbd>Shift</kbd>+
  <kbd>←</kbd>/<kbd>→</kbd>, so windows briefly take focus while it works.
- Windows running **as administrator** can't be moved by a non-elevated
  HopperDock. Run the dock elevated too if you need that.
- Tiling and "move all" skip minimized windows; saved layouts do not.
- `HopperHome` (`move_dock_home.pyw`) is a tiny bundled helper that yanks the
  dock back to your primary monitor — handy as a taskbar shortcut if you ever
  unplug a screen while the dock is on it.

---

## Contributing

Issues and pull requests are welcome. The whole dock is one file
(`window_dock.pyw`) with no build step for development — edit it and run
`run_dock_visible.bat` to see changes with a console attached.

## License

[MIT](LICENSE) — do whatever you like with it, just keep the copyright notice.

---

<div align="center">

**Made by ScaryLasers**

[YouTube](https://www.youtube.com/@ScaryLasers) •
[Twitch](https://twitch.tv/scarylasers) •
[Instagram](https://www.instagram.com/scarylasers_) •
[TikTok](https://www.tiktok.com/@scarylasers)

<sub>If HopperDock saves you some clicks, come say hi. 🐰</sub>

</div>
