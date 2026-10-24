"""Merge our v4l2loopback device into whatever other plugins already declared.

Arch systems often have more than one virtual camera (camera-effects, OBS,
this plugin). modprobe reads /etc/modprobe.d/*.conf in lexical order and the
last value of a parameter wins, so this module produces a single, complete
`options v4l2loopback` line in `zz-iphone-camera.conf` that preserves every
existing device and appends ours.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

LABEL = "iPhone Camera"
NUMBER = 43
OPTIONS = re.compile(r"^options\s+v4l2loopback(\s|$)")


def scan(confdir: Path, skip: Path | None = None) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {}
    for path in sorted(confdir.glob("*.conf")):
        if skip is not None and path == skip:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or not OPTIONS.match(line):
                continue
            try:
                tokens = shlex.split(line)
            except ValueError:
                continue
            for token in tokens[2:]:
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                params.setdefault(key, []).append(value)
    return params


def _numbers(values: list[str]) -> list[int]:
    out = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if re.fullmatch(r"-?\d+", part):
                out.append(int(part))
    return out


def _labels(values: list[str]) -> list[str]:
    out = []
    for value in values:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _quote(label: str) -> str:
    return f'"{label}"' if (" " in label or "," in label) else label


def build(params: dict[str, list[str]], label: str = LABEL, number: int = NUMBER) -> str:
    video_nr = _numbers(params.get("video_nr", []))
    card_label = _labels(params.get("card_label", []))

    if number not in video_nr and label not in card_label:
        video_nr.append(number)
        card_label.append(label)

    # Every device needs an entry; pad from the last known value.
    while len(card_label) < len(video_nr):
        card_label.append(f"Virtual Camera {len(card_label) + 1}")

    exclusive = [p.strip() for p in params.get("exclusive_caps", ["1"])[0].split(",") if p.strip()]
    while len(exclusive) < len(video_nr):
        exclusive.append(exclusive[-1] if exclusive else "1")

    fields = [
        "options v4l2loopback",
        f"devices={len(video_nr)}",
        "video_nr=" + ",".join(str(n) for n in video_nr),
        "card_label=" + ",".join(_quote(l) for l in card_label[: len(video_nr)]),
        "exclusive_caps=" + ",".join(exclusive[: len(video_nr)]),
        "max_buffers=" + params.get("max_buffers", ["2"])[0],
    ]
    return " ".join(fields)


HEADER = (
    "# Written by omarchy-iphone-camera. Merged with the other v4l2loopback\n"
    "# device definitions on this machine so every consumer keeps its node.\n"
)


def merge_and_write(confdir: Path, out: Path, label: str = LABEL, number: int = NUMBER) -> str:
    params = scan(confdir, skip=out)
    line = build(params, label=label, number=number)
    out.write_text(HEADER + line + "\n")
    return line
