# omarchy-iphone-camera

Use an iPhone as the webcam for any meeting app on [Omarchy](https://omarchy.org) — Zoom, Meet,
Teams, Discord, Slack, OBS, browsers, anything that can pick a camera.

The phone streams over **Wi-Fi** (or USB). The desktop exposes it as a normal
`/dev/video*` device, so no app needs a plugin, driver or account.

## Why not Bluetooth?

Because it cannot work, and it is worth writing down so nobody tries again:

| | |
|---|---|
| **iOS has no video profile** | iPhone supports HFP/HSP, A2DP, AVRCP, PBAP, MAP, PAN and HID. There is no camera or video profile, and no public API to push frames over Bluetooth. |
| **Bandwidth** | Bluetooth Classic EDR ≈ 2.1 Mbit/s theoretical, ~1.5 Mbit/s real. BLE 5 ≈ 1.4 Mbit/s real. |
| **Video needs** | 720p30 H.264 ≈ 2–2.5 Mbit/s, 1080p30 ≈ 4–6 Mbit/s, 4K ≈ 15–25 Mbit/s. |
| **Apple's own answer** | Continuity Camera uses Bluetooth/BLE only for proximity and handshake; the media goes over Wi-Fi (AWDL) or USB. |

So the plugin uses Bluetooth for exactly one thing: `bluetoothDevice` in the
settings makes the bar show "iPhone nearby". Video always takes Wi-Fi or USB.

## Architecture

```
 iPhone (Safari)                    Omarchy desktop
┌──────────────────┐   WSS/HTTPS   ┌──────────────────────────────────────┐
│ getUserMedia     │ ────────────► │ iphone-camera daemon                 │
│ canvas → JPEG    │   1280×720    │  ├─ HTTPS + WebSocket server (:4711) │
│ 30 fps, ~2 Mbit  │               │  ├─ ffmpeg: MJPEG → YUV420P          │
└──────────────────┘               │  └─ /dev/video43 "iPhone Camera"     │
                                   └──────────────┬───────────────────────┘
                                                  │
                          ┌───────────────────────┴──────────────────────┐
                          ▼                                              ▼
                 camera-effects (/dev/video42)                 Zoom / Meet / OBS
                 Center Stage, Portrait, blur                  see a plain webcam
```

Nothing is installed on the phone: the daemon serves a small page, the phone
opens it in Safari, and `getUserMedia` does the rest. The daemon writes to its
own loopback device so it composes with `camera-effects` instead of fighting it.

## Requirements

- Omarchy 4 (Quickshell bar) on Arch
- iPhone with iOS 14.3+ (Safari `getUserMedia`), any model
- `v4l2loopback-dkms`, `ffmpeg`, `v4l-utils`, `qrencode` — installed by setup
- Phone and desktop on the same Wi-Fi (or the phone's hotspot)

## Install

```bash
git clone https://github.com/randyellis-wealthberry/omarchy-iphone-camera
cd omarchy-iphone-camera
./install.sh
```

`install.sh` asks for your password once, for the loopback kernel module. It
merges the module options, so an existing `camera-effects` (or OBS) virtual
camera keeps working and gets its own node.

Manual install, if you prefer:

```bash
sudo install -Dm644 share/modprobe.conf /etc/modprobe.d/zz-iphone-camera.conf
sudo modprobe v4l2loopback
ln -sf "$PWD/bin/iphone-camera" ~/.local/bin/
install -m644 share/iphone-camera.service ~/.config/systemd/user/
systemctl --user enable --now iphone-camera
```

## Use

1. Click the camera icon in the bar.
2. **Scan with iPhone** → Camera app → open the link in Safari.
3. Accept the certificate warning once (*Show Details → visit this website*).
4. Tap **Start**, keep Safari in the foreground.
5. In the meeting app, pick **iPhone Camera** (`/dev/video43`).

The device is always visible to apps. Until the phone connects it shows a
still "waiting for the phone" frame, which is what keeps v4l2loopback in
capture mode (see [Why a still frame?](#why-a-still-frame)).

### CLI

```bash
iphone-camera status          # human readable
iphone-camera status --json   # what the bar widget reads
iphone-camera qr              # QR code in the terminal
iphone-camera copy            # phone URL to the clipboard
iphone-camera resolution 1080p
iphone-camera set jpegQuality 0.85
iphone-camera doctor          # diagnose install/permissions
iphone-camera logs -f
```

Settings live in `~/.config/iphone-camera/settings.json`; runtime state in
`~/.local/state/iphone-camera/`. Both are plain JSON.

## What to expect

| Path | Latency (glass to glass) | Notes |
|---|---|---|
| Wi-Fi 5/6, 720p30 | 180–350 ms | Typical; fine for calls, not for gaming |
| Wi-Fi, 1080p30 | 250–450 ms | 4–6 Mbit/s; needs a decent 5 GHz link |
| USB (Iriun backend) | 80–150 ms | Wired, charges the phone |
| Bluetooth | — | Not possible, see above |

The virtual camera is 720p30 by default because that is what meeting apps
actually send to the other side. 1080p is available when you record instead.

## Why a still frame?

`v4l2loopback` runs with `exclusive_caps=1` because that is what browsers and
WebRTC apps require. In that mode the device only announces CAPTURE while a
producer is attached; with no producer it looks like an *output* device and
simply disappears from camera pickers.

So the daemon keeps a producer attached permanently and re-sends a still frame
five times a second while the phone is idle. The device is therefore always
listed in Zoom/Meet/Teams, showing "waiting for the phone" until real frames
arrive. This is also why `iphone-camera status` reports `waiting` rather than
`stopped` once the service is up.

## Security

- The phone URL carries a random token (`/?t=…`), so a neighbour on the same
  Wi-Fi cannot push video into your webcam by guessing the port.
- TLS is a self-signed certificate generated on first run, with a SAN for the
  current LAN address. Regenerated automatically when your IP changes.
- The daemon only accepts JPEG frames from a connected, token-bearing client.
  It never reads the desktop's camera.
- Nothing leaves the LAN; there is no cloud service and no account.

Binding to `0.0.0.0` is required for the phone to reach the desktop. If you do
not trust the network, use the phone's hotspot or a USB backend.

## Troubleshooting

**Safari says it cannot connect to the server.** Usually a host firewall. If
`ufw` is active it drops the port by default (`iphone-camera doctor` says so):

```bash
sudo ufw allow from 192.168.0.0/24 to any port 4711 proto tcp
```

Also confirm the phone is on the *same* network — a guest SSID with client
isolation, a different VLAN, or cellular will all fail the same way.

**Safari says the certificate is invalid.** Expected: the certificate is
self-signed. *Show Details → visit this website*. If Safari refuses entirely,
check that the URL uses the same IP the desktop currently has
(`iphone-camera url`), because the SAN is per-address.

**The page loads but Start does nothing.** Camera access needs a secure
context; make sure you opened `https://`, not `http://`. Allow camera access in
*Settings → Safari → Camera*.

**Black frames in the meeting app.** Another process may hold `/dev/video43`.
`iphone-camera doctor` and `fuser -v /dev/video43` will say. Only one writer is
allowed per loopback node.

**The camera is missing from the app's list.** Check that the service is
running (`iphone-camera status` should say `waiting`, not `stopped`). Without a
producer, v4l2loopback hides the device in `exclusive_caps` mode; the daemon's
still frame is what keeps it visible.

**`/dev/video43` is missing.** `iphone-camera setup`, then reboot if the module
was busy during install.

**Choppy video.** Lower the resolution (`iphone-camera resolution 540p`) or the
quality (`iphone-camera set jpegQuality 0.6`). 2.4 GHz Wi-Fi is the usual cause.

**Bar icon missing.** `omarchy-shell shell rescanPlugins`, then
`omarchy plugin enable iphone-camera`.

## Roadmap

- [ ] USB backend via Iriun (`iriunwebcam-bin`) for wired, low-latency capture
- [ ] Audio from the iPhone mic (currently video only)
- [ ] Multiple phones / camera switching in the widget
- [ ] Optional NDI and RTSP inputs for other phones
- [ ] Package for the AUR

## Uninstall

```bash
./uninstall.sh          # keeps settings
./uninstall.sh --purge  # also removes settings and the loopback config
```

## License

MIT. See [LICENSE](LICENSE).
