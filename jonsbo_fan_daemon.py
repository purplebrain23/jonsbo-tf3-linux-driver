#!/usr/bin/env python3
"""
Jonsbo TF3 fan-panel USB daemon - keeps the 3 fan-panel USB interfaces
claimed permanently and accepts already-rendered images over a Unix socket
(see jonsbo_fan_lib.send_fan_frames()).

Why USB claiming and rendering are split into two processes (see README.md):
every claim/release cycle on the 3 fan panels risks wedging them (panels go
black, the command/ACK layer stays responsive, only a physical power cycle
recovers it). As long as USB claiming and rendering lived in the same
process, every code deploy (systemctl restart) also re-claimed the USB
connection and reliably wedged the panels. This daemon is meant to be
started once and never restarted again after that - rendering/logic changes
go through the separate, safely-restartable jonsbo_monitor.py client, which
never touches USB directly.
"""
import io
import os
import socket
import struct
import sys
import time

from PIL import Image

from jonsbo_fan_lib import find_panels, install_sigterm_handler, SOCK_PATH

STALE_AFTER = 30  # s without a new frame for a panel -> next frame gets a full warmup


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_frames(conn):
    """Reads one frame packet: 1 byte count, then per panel (1 byte idx,
    4 byte big-endian length, PNG bytes). Returns dict idx->PIL.Image, or
    None on a dropped connection/malformed packet."""
    head = recv_exact(conn, 1)
    if head is None:
        return None
    count = head[0]
    out = {}
    for _ in range(count):
        h = recv_exact(conn, 5)
        if h is None:
            return None
        idx, length = struct.unpack(">BI", h)
        data = recv_exact(conn, length)
        if data is None:
            return None
        out[idx] = Image.open(io.BytesIO(data)).convert("RGB")
    return out


def process_frames(panels, frames, warmed, last_seen):
    """If ANY panel in this packet is fresh (never warmed up) or hasn't been
    fed in STALE_AFTER seconds, all panels included in this packet get
    pushed 30x interleaved - a claimed-but-unfed panel otherwise goes black
    while its neighbors are being pushed frames, so all panels a client
    cares about need to be warmed up together, in lockstep."""
    now = time.time()
    needs_warmup = any(
        idx < len(panels) and (not warmed[idx] or (now - last_seen[idx]) > STALE_AFTER)
        for idx in frames
    )
    if needs_warmup:
        for _ in range(30):
            for idx, img in frames.items():
                if idx >= len(panels):
                    continue
                try:
                    panels[idx].push_image(img)
                except Exception as e:
                    print(f"[{panels[idx].label}] warmup error: {e}")
            time.sleep(0.05)
        for idx in frames:
            if idx < len(panels):
                warmed[idx] = True
    else:
        for idx, img in frames.items():
            if idx >= len(panels):
                continue
            try:
                panels[idx].push_image(img)
            except Exception as e:
                print(f"[{panels[idx].label}] push error: {e}")
    for idx in frames:
        if idx < len(panels):
            last_seen[idx] = now


def main():
    print("Jonsbo Fan-Daemon - keeps 3 fan panels claimed, Ctrl+C to stop")
    panels = find_panels()
    if not panels:
        print("No fan panels found - exiting.")
        sys.exit(1)

    shutdown_requested = install_sigterm_handler()

    # Claim ALL panels first, THEN send init commands on all of them - see
    # FanPanel.open() docstring.
    for p in panels:
        p.open(claim_only=True, settle=0.3)
    for p in panels:
        p.init_commands()

    warmed = [False] * len(panels)
    last_seen = [0.0] * len(panels)

    if os.path.exists(SOCK_PATH):
        os.remove(SOCK_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o600)
    srv.listen(1)
    srv.settimeout(1.0)
    print(f"Socket ready: {SOCK_PATH}")

    try:
        while not shutdown_requested.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    frames = recv_frames(conn)
                except Exception as e:
                    print(f"Receive error: {e}")
                    continue
                if not frames:
                    continue
                try:
                    process_frames(panels, frames, warmed, last_seen)
                except Exception as e:
                    print(f"Error while processing: {e}")
                try:
                    conn.sendall(b"\x01")
                except OSError:
                    pass
    finally:
        print("Shutting down cleanly (no transfers in flight), closing panels...")
        for p in panels:
            p.close()
        try:
            srv.close()
        except OSError:
            pass
        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)


if __name__ == "__main__":
    main()
