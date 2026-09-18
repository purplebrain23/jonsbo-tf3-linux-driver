# jonsbo-tf3-linux-driver

Unofficial Linux driver for the 3 fan-mounted LCD panels on the Jonsbo
TF3-360SCB AIO liquid cooler (USB `43a8:0e61`), reverse-engineered from
scratch. As far as I'm aware this is the first working Linux driver for
these panels — previously the only way to drive them was the official
Windows JONSBO app.

Not affiliated with or endorsed by Jonsbo. Provided as-is, no warranty —
see [LICENSE](LICENSE).

## What's here

- `jonsbo_fan_lib.py` — the actual driver: USB protocol, DES command
  channel, frame encoding.
- `jonsbo_fan_daemon.py` — a small daemon that claims the 3 panels once and
  serves rendered frames over a Unix socket. See "Why a daemon?" below —
  this isn't incidental complexity, it works around a real hardware quirk.
- `jonsbo_monitor.py` — an example client: renders a CPU/RAM/GPU/VRAM +
  clock dashboard and sends it to the daemon every 2s. Optionally also
  drives the cooler's separate 640x480 pump display (via the third-party
  [turing-smart-screen-cli](https://github.com/mathoudebine/turing-smart-screen-python)
  tool — different USB device, not covered by this repo) and can show a
  live FPS overlay sourced from [MangoHud](https://github.com/flightlessmango/MangoHud)
  logs while a game is running.
- `examples/send_test_image.py` — standalone script to sanity-check a
  single panel without the daemon, and to figure out which panel index maps
  to which physical position on your cable routing.
- `systemd/` — user-unit templates for running the daemon + monitor
  persistently.
- `udev/` — udev rule for non-root USB access.

## Hardware background

The TF3-360SCB has 3 small LCD panels mounted on the fans plus one larger
display on the pump block. The 3 fan panels enumerate as **3 separate USB
devices**, all `idVendor=43a8 idProduct=0e61`, sitting behind a shared,
non-switchable internal USB hub. The pump display is a different, unrelated
USB device (`1cbe:0035`) already supported by the existing
turing-smart-screen-python/cli project — this repo only concerns itself
with the 3 fan panels.

## Protocol

Reconstructed by capturing USB traffic from the official Windows app and
decompiling it (Dotfuscator-obfuscated .NET, cleaned with de4dot) to read
the class actually driving the panels. No vendor code is included in this
repo — only the protocol knowledge extracted from it, implemented from
scratch in `jonsbo_fan_lib.py`.

- **Command channel**: 32-byte packets over an interrupt endpoint (not the
  bulk endpoint used for frame data). Byte 0-1 = `0xAA 0x55` magic, byte 10
  = channel id (`0x01` = command, `0x02` = frame), byte 31 = `0xBB`
  terminator. Command payload (bytes 2-9) is DES-ECB encrypted with a fixed
  8-byte key embedded in the vendor app.
- **Init sequence**: cmd `56` (init) → cmd `84` (brightness, 0-100) → cmd
  `86` (rotation).
- **Frame push**: image data is sent as a stream of 32-byte-header + 480-byte-
  payload chunks over the bulk endpoint. After the *first* frame following a
  fresh connection, the panel's status response decrypts to `0x60` and a
  **commit command (`88`)** must be sent once to actually latch the frame —
  subsequent frames report `0x62` and need no further commit. This "commit"
  step was the actual missing piece in ~20 sessions of earlier RE attempts;
  everything else about the transfer looked correct without it, the panel
  would just never update.
- **Framebuffer**: native orientation is **180×640 portrait**, not 640×180
  landscape as the panel's physical mounting suggests — the controller
  sits rotated 90° inside the fan housing, and the vendor app rotates
  content before sending. This library accepts normal 640×180 landscape
  images and rotates internally.
- **Pixel order**: **BGR**, not RGB.
- A single `push_image()` after a cold boot is not enough to switch the
  panel off its boot logo — it needs to be pushed repeatedly (`warmup()`
  handles this).

## Why a daemon? (the hardware's most annoying quirk)

On this specific hardware, **claiming or releasing the USB interface on
these panels is itself risky**: a live restart of a process that directly
holds the panels open reliably (repeatedly, reproducibly) leaves all 3
panels solid black — the command/ACK layer keeps responding normally, but
the render pipeline stays wedged. The only confirmed recovery from that
state is a full physical power cycle (PSU off/on) — a plain OS reboot also
recovers it, but a live `systemctl restart` of a process holding the USB
claim does not.

This turned out to be structural, not a bug in any particular shutdown
handler — rewriting signal handling to guarantee a clean shutdown didn't
fix it; a "rotating claim" architecture that briefly claims/releases one
panel at a time made it *worse*. What did work: **splitting USB ownership
from rendering into two processes**. `jonsbo_fan_daemon.py` claims the 3
panels once at boot and is never meant to restart. `jonsbo_monitor.py` only
renders images and sends them over a Unix socket — it never touches USB, so
it can be freely restarted (new features, layout tweaks, bug fixes) without
ever re-triggering the risky claim/release cycle.

If you only need one process and don't care about live-restarting your
rendering logic, you could merge the two — but expect any restart of that
merged process to risk wedging the panels on this hardware.

## Install

```bash
git clone https://github.com/purplebrain23/jonsbo-tf3-linux-driver.git ~/jonsbo-tf3-linux-driver
cd ~/jonsbo-tf3-linux-driver
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

sudo cp udev/99-jonsbo-tf3.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Unplug/replug the cooler (or reboot) after installing the udev rule so the
new permissions take effect.

### Find your panel mapping

Panel index → physical position depends on cable routing and isn't fixed
across units:

```bash
.venv/bin/python3 examples/send_test_image.py 0
.venv/bin/python3 examples/send_test_image.py 1
.venv/bin/python3 examples/send_test_image.py 2
```

Each should light up one physical panel with colored edges (top=yellow,
bottom=blue, left=red, right=green) so you can tell them apart. Note which
index is which physical panel — you'll need this for `jonsbo_monitor.py`'s
`PANEL_RENDER` mapping.

### Run persistently

```bash
mkdir -p ~/.config/systemd/user
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now jonsbo-fan-daemon.service
systemctl --user enable --now jonsbo-monitor.service
```

`jonsbo-monitor.service` is safe to restart at will
(`systemctl --user restart jonsbo-monitor.service`) while you iterate on
`jonsbo_monitor.py`. Avoid restarting `jonsbo-fan-daemon.service` on
already-running hardware unless you're prepared to power-cycle if it wedges
— see "Why a daemon?" above. Both units use `Restart=no` deliberately, so a
crash doesn't trigger an automatic re-claim.

### Customize the dashboard

Edit `render_fan_a`/`render_fan_b`/`render_fan_c` and `render_pump_idle` in
`jonsbo_monitor.py`. GPU stats default to NVIDIA via `nvidia-smi` — adapt
`get_gpu()` for other vendors.

### Optional: pump display

The pump's 640×480 display (`1cbe:0035`) is a *different* USB device from
the 3 fan panels and isn't reverse-engineered in this repo — it's driven
through a separate third-party tool,
[turingscreencli](https://pypi.org/project/turingscreencli/) ("Turing Smart
Screen CLI" by Study Hsueh).

**The one non-obvious step:** that tool's PyPI release only targets the
*Turing Smart Screen 8.8" V1.1* (`idProduct=0x0088`) — a different, larger
display from the same OEM. It doesn't recognize our 3.5" `0x0035` device out
of the box. It works anyway once you clone the tool from source and change
one line:

```
src/turingscreencli/transport.py:
    PRODUCT_ID = 0x0088   # -> change to 0x0035
```

After that, `pip install -e .` gives you the `turing-screen` console script
used by `jonsbo_monitor.py` (`send-image --path ...`). Both devices are
apparently protocol-compatible enough at the level this tool operates on
that only the product ID needed to change — nothing else.

Install it, patch that line, make sure the resulting `turing-screen` binary
is on `PATH` (or set `TURING_SCREEN_CLI` to its full path), and add a udev
rule for `1cbe:0035` (see the commented-out line in `udev/99-jonsbo-tf3.rules`).
If `turing-screen` isn't found, `jonsbo_monitor.py` skips the pump-display
step and only drives the 3 fan panels.

### Optional: MangoHud FPS overlay on the pump display

Configure MangoHud to log CSVs to a folder (`output_folder=...`,
`autostart_log=1` in `MangoHud.conf`), then set `MANGOHUD_CSV_DIR` to that
folder (defaults to `~/mangologs`). While a log file is fresh (<8s old),
the pump display shows FPS/frametime/power instead of the idle clock.

MangoHud still has to actually be *active* in the game for anything to be
logged. Enabling it per-game (Steam launch options `mangohud %command%`) is
the usual advice, but it's easy to forget on a newly installed game — and
the symptom is indistinguishable from a broken display: the pump just stays
on the idle screen. Enabling it globally instead is more robust for this
use case:

```sh
echo 'MANGOHUD=1' > ~/.config/environment.d/mangohud.conf   # then re-login
```

The implicit Vulkan layer (`/usr/share/vulkan/implicit_layer.d/MangoHud.*.json`)
is picked up from that variable alone, so the `mangohud` wrapper script is
not needed for Vulkan titles. OpenGL titles still need the wrapper.

Caveat: globally, *every* Vulkan application logs, not just games.
`get_mango_metrics()` always picks the newest CSV in the folder, so a
non-game that writes one would be shown on the pump display. Add those to
MangoHud's `blacklist` if it becomes a problem.

## Credits

Protocol reverse-engineered via USB capture analysis (Wireshark/USBPcap)
and decompilation of the vendor's Windows application for interoperability
purposes. No vendor code or assets are redistributed here.
