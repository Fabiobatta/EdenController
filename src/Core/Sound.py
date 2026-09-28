"""
Core/Sound.py
Short interface sounds: a player joining, confirming, the roulette...

The sounds are synthesised (a few sine notes each) into WAV files in the
temp folder the first time the launcher runs, on a background thread, so
the launcher ships no audio files. A WAV with the same name in the
"sounds" folder next to the launcher replaces the built-in one.

Playback never blocks the UI loop:
    Windows      winsound, asynchronous (a new sound cuts the previous one)
    Linux/macOS  paplay / aplay / afplay in a child process, if installed

    sounds = Sounds(enabled=True, volume=70, override_dir="...")
    sounds.play("join", 2)      # the chime for player 2
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading

from .Log import log

RATE = 22050
FADE_S = 0.004              # attack, avoids clicks

# C major pentatonic from C5: any sequence of these sounds pleasant
_NOTES = [523.25, 587.33, 659.25, 783.99, 880.00, 1046.50, 1174.66, 1318.51, 1567.98, 1760.00]


def _note(index):
    return _NOTES[index % len(_NOTES)] * (2 ** (index // len(_NOTES)))


# name -> [(start s, frequency Hz, duration s, gain)]
SOUNDS = {
    "move":    [(0.0, 1760.0, 0.028, 0.18)],
    "tick":    [(0.0, 1318.5, 0.035, 0.30)],
    "select":  [(0.0, _note(2), 0.09, 0.55), (0.07, _note(5), 0.16, 0.55)],
    "back":    [(0.0, _note(4), 0.07, 0.40), (0.06, _note(1), 0.12, 0.40)],
    "leave":   [(0.0, _note(5), 0.08, 0.45), (0.07, _note(3), 0.08, 0.45), (0.14, _note(0), 0.16, 0.45)],
    "error":   [(0.0, 220.0, 0.10, 0.45), (0.11, 196.0, 0.16, 0.45)],
    "toggle":  [(0.0, _note(3), 0.06, 0.45), (0.05, _note(7), 0.12, 0.45)],
    "launch":  [(0.0, _note(0), 0.12, 0.45), (0.08, _note(2), 0.12, 0.45), (0.16, _note(4), 0.12, 0.45),
                (0.24, _note(5), 0.45, 0.55)],
    "win":     [(0.0, _note(0), 0.10, 0.5), (0.09, _note(2), 0.10, 0.5), (0.18, _note(4), 0.10, 0.5),
                (0.27, _note(5), 0.10, 0.5), (0.38, _note(7), 0.10, 0.5), (0.49, _note(10), 0.55, 0.6),
                (0.49, _note(7), 0.55, 0.35)],
}
# Joining: one blip per pulse of the confirmation rumble, higher for each
# player, and a brighter last one
for _player in range(1, 9):
    SOUNDS[f"blip{_player}"] = [(0.0, _note(_player - 1), 0.10, 0.5)]
    SOUNDS[f"join{_player}"] = [(0.0, _note(_player - 1), 0.08, 0.5), (0.06, _note(_player + 1), 0.08, 0.45),
                                (0.12, _note(_player + 3), 0.22, 0.5)]


def synthesize(notes, volume=1.0):
    """16-bit mono PCM for a list of notes (sine + a soft octave, exponential decay)."""
    length = max(start + duration for start, _, duration, _ in notes) + 0.02
    samples = [0.0] * int(length * RATE)
    for start, frequency, duration, gain in notes:
        first = int(start * RATE)
        count = int(duration * RATE)
        step = 2 * math.pi * frequency / RATE
        decay = 5.0 / count
        fade = FADE_S * RATE
        for i in range(count):
            envelope = math.exp(-i * decay) * min(1.0, i / fade)
            samples[first + i] += gain * envelope * (math.sin(i * step) + 0.25 * math.sin(2 * i * step))
    scale = 32767 * 0.8 * max(0.0, min(1.0, volume))
    return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * scale)) for v in samples)


def wav(pcm):
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, 1, 1,
                         RATE, RATE * 2, 2, 16, b"data", len(pcm))
    return header + pcm


def _player_command():
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay"]
    for tool in ("paplay", "aplay"):
        if shutil.which(tool):
            return [tool] + (["-q"] if tool == "aplay" else [])
    return None


class Sounds:
    def __init__(self, enabled=True, volume=70, override_dir=None, folder=None):
        self.enabled = enabled
        self.volume = max(0, min(100, volume)) / 100
        self.override_dir = override_dir
        self.folder = folder or os.path.join(tempfile.gettempdir(), "EdenLauncherSounds")
        self.files = {}
        self._command = None
        if not enabled:
            return
        if sys.platform != "win32":
            self._command = _player_command()
            if not self._command:
                log("INFO", "No audio player found (paplay/aplay) - sounds off")
                self.enabled = False
                return
        threading.Thread(target=self._prepare, daemon=True).start()

    def _prepare(self):
        """Write every sound once (background thread); play() skips sounds not ready yet."""
        try:
            os.makedirs(self.folder, exist_ok=True)
            tag = f"v1_{int(self.volume * 100)}"
            for name, notes in SOUNDS.items():
                custom = os.path.join(self.override_dir or "", name + ".wav")
                if self.override_dir and os.path.isfile(custom):
                    self.files[name] = custom
                    continue
                path = os.path.join(self.folder, f"{name}_{tag}.wav")
                if not os.path.isfile(path):
                    with open(path + ".part", "wb") as f:
                        f.write(wav(synthesize(notes, self.volume)))
                    os.replace(path + ".part", path)
                self.files[name] = path
        except Exception as e:
            log("WARNING", "Could not prepare sounds", e)

    def play(self, name, player=None):
        """Play `name` ("join" + player 2 -> "join2") without waiting."""
        if not self.enabled:
            return
        path = self.files.get(f"{name}{player}" if player else name)
        if not path:
            return
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            else:
                subprocess.Popen(self._command + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log("WARNING", "Sound failed", f"{name}: {e}")
            self.enabled = False
