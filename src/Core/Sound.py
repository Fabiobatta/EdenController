"""
Core/Sound.py
Short interface sounds: a player joining, confirming, the roulette...

The sounds are synthesised (a few sine notes each) into WAV files in the
temp folder the first time the launcher runs, on a background thread, so
the launcher ships no audio files. A WAV with the same name in the
"sounds" folder next to the launcher replaces the built-in one.

Playback never blocks the UI loop:
    Windows      a small mixer thread feeding the default output (winmm
                 waveOut): sounds overlap instead of cutting each other,
                 and the stream stays open while the launcher is on screen,
                 so a device that sleeps when idle (HDMI, Bluetooth, the
                 virtual speaker of a streaming host) never swallows the
                 start of a sound. winsound if the device cannot be opened.
    Linux/macOS  paplay / aplay / afplay in a child process, if installed

    sounds = Sounds(enabled=True, volume=70, override_dir="...")
    sounds.play("join", 2)      # the chime for player 2
    sounds.suspend()            # a game is starting: free the device when idle
    sounds.resume()             # back on screen: keep it open again
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from array import array

from .Log import log

RATE = 22050
FADE_S = 0.004              # attack, avoids clicks
TAIL_S = 0.06               # silence after each sound: players that stop early cut silence, not the note
MIX_BLOCK = 512             # samples per mixer buffer (~23 ms)
MIX_BUFFERS = 4             # queued buffers: ~90 ms between play() and the speaker
MAX_VOICES = 8

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
# Achievement: a bell-like arpeggio, higher and longer than "win"
SOUNDS["trophy"] = [(0.0, _note(5), 0.12, 0.45), (0.08, _note(7), 0.12, 0.45), (0.16, _note(9), 0.12, 0.45),
                    (0.26, _note(10), 0.7, 0.55), (0.26, _note(12), 0.7, 0.3)]

# Joining: one blip per pulse of the confirmation rumble, higher for each
# player, and a brighter last one
for _player in range(1, 9):
    SOUNDS[f"blip{_player}"] = [(0.0, _note(_player - 1), 0.10, 0.5)]
    SOUNDS[f"join{_player}"] = [(0.0, _note(_player - 1), 0.08, 0.5), (0.06, _note(_player + 1), 0.08, 0.45),
                                (0.12, _note(_player + 3), 0.22, 0.5)]


def synthesize(notes, volume=1.0):
    """16-bit mono PCM for a list of notes (sine + a soft octave, exponential decay)."""
    length = max(start + duration for start, _, duration, _ in notes) + TAIL_S
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


# ============================================================================
# MIXER
# ============================================================================
class Mixer:
    """Sums the sounds that are playing into blocks of 16-bit mono PCM."""

    def __init__(self, max_voices=MAX_VOICES):
        self.max_voices = max_voices
        self._voices = []                   # [samples (array 'h'), position]
        self._lock = threading.Lock()

    def add(self, samples):
        with self._lock:
            if len(self._voices) >= self.max_voices:
                self._voices.pop(0)             # the oldest makes room
            self._voices.append([samples, 0])

    @property
    def busy(self):
        return bool(self._voices)

    def render(self, count):
        """The next `count` samples as bytes (silence when nothing plays)."""
        with self._lock:
            voices = self._voices
            if not voices:
                return bytes(2 * count)
            if len(voices) == 1:
                samples, position = voices[0]
                block = samples[position:position + count]
                voices[0][1] += count
                if voices[0][1] >= len(samples):
                    voices.clear()
                out = block.tobytes()
                return out + bytes(2 * count - len(out))
            mixed = [0] * count
            for voice in voices:
                samples, position = voice
                for i, value in enumerate(samples[position:position + count]):
                    mixed[i] += value
                voice[1] += count
            self._voices = [v for v in voices if v[1] < len(v[0])]
        return array("h", [32767 if v > 32767 else -32768 if v < -32768 else v for v in mixed]).tobytes()


def load_pcm(path):
    """A WAV file as 16-bit mono samples at RATE (other rates resampled), or None."""
    try:
        with wave.open(path, "rb") as f:
            channels, width, rate = f.getnchannels(), f.getsampwidth(), f.getframerate()
            frames = f.readframes(f.getnframes())
    except (OSError, EOFError, wave.Error):
        return None
    if width != 2 or channels < 1:
        return None
    samples = array("h")
    samples.frombytes(frames[:len(frames) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = array("h", (sum(samples[i:i + channels]) // channels
                              for i in range(0, len(samples) - channels + 1, channels)))
    if rate != RATE and samples:
        step = rate / RATE
        samples = array("h", (samples[min(len(samples) - 1, int(i * step))]
                              for i in range(int(len(samples) / step))))
    return samples


class _WaveOut:
    """The default Windows output device through winmm, fed by a Mixer on a thread."""

    WAVE_MAPPER = 0xFFFFFFFF
    WHDR_DONE = 0x1
    IDLE_CLOSE_S = 1.0                      # suspended: close this long after the last sound

    def __init__(self, mixer):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.winmm = ctypes.WinDLL("winmm")
        self.mixer = mixer
        self.keep_open = True
        self._wake = threading.Event()
        self._closed = False
        self._warned = False

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                        ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                        ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                        ("cbSize", wintypes.WORD)]

        class WAVEHDR(ctypes.Structure):
            pass
        WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                            ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                            ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                            ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]
        self.WAVEHDR = WAVEHDR
        self.format = WAVEFORMATEX(1, 1, RATE, RATE * 2, 2, 16, 0)
        self.handle = None
        self.open()                         # raises if there is no usable device
        threading.Thread(target=self._run, daemon=True).start()

    def open(self):
        ctypes = self.ctypes
        handle = ctypes.c_void_p()
        result = self.winmm.waveOutOpen(ctypes.byref(handle), ctypes.c_uint(self.WAVE_MAPPER),
                                        ctypes.byref(self.format), None, None, 0)
        if result != 0:
            raise OSError(f"waveOutOpen failed ({result})")
        self.handle = handle
        self.buffers = [ctypes.create_string_buffer(MIX_BLOCK * 2) for _ in range(MIX_BUFFERS)]
        self.headers = []
        for buffer in self.buffers:
            header = self.WAVEHDR()
            header.lpData = ctypes.cast(buffer, ctypes.c_void_p).value
            header.dwBufferLength = MIX_BLOCK * 2
            self.winmm.waveOutPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
            header.dwFlags |= self.WHDR_DONE        # free to fill
            self.headers.append(header)

    def close(self):
        if self.handle is None:
            return
        ctypes, handle = self.ctypes, self.handle
        self.handle = None
        self.winmm.waveOutReset(handle)
        for header in self.headers:
            self.winmm.waveOutUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
        self.winmm.waveOutClose(handle)

    def wake(self):
        self._wake.set()

    def shutdown(self):
        """Stop the thread and release the device (the settings screen replaces the mixer)."""
        self._closed = True
        self._wake.set()

    def _run(self):
        ctypes = self.ctypes
        last_sound = time.monotonic()
        while not self._closed:
            try:
                if self.handle is None:
                    if not (self.keep_open or self.mixer.busy):
                        self._wake.wait(0.5)
                        self._wake.clear()
                        continue
                    self.open()
                if self.mixer.busy:
                    last_sound = time.monotonic()
                elif not self.keep_open and time.monotonic() - last_sound > self.IDLE_CLOSE_S:
                    self.close()
                    continue
                for header, buffer in zip(self.headers, self.buffers):
                    if header.dwFlags & self.WHDR_DONE:
                        ctypes.memmove(buffer, self.mixer.render(MIX_BLOCK), MIX_BLOCK * 2)
                        result = self.winmm.waveOutWrite(self.handle, ctypes.byref(header),
                                                         ctypes.sizeof(header))
                        if result != 0:         # e.g. the device was unplugged
                            raise OSError(f"waveOutWrite failed ({result})")
            except Exception as e:
                if not self._warned:
                    log("WARNING", "Sound output failed, reopening", e)
                    self._warned = True
                try:
                    self.close()
                except Exception:
                    self.handle = None
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            time.sleep(0.005)
        try:
            self.close()
        except Exception:
            pass


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
        self.pcm = {}
        self._ready = False
        self._pending = None            # asked for before the files were ready: played once they are
        self._command = None
        self._mixer = None
        self._output = None
        if not enabled:
            return
        if sys.platform == "win32":
            try:
                self._mixer = Mixer()
                self._output = _WaveOut(self._mixer)
            except Exception as e:
                log("WARNING", "No mixer output, using winsound (sounds cut each other)", e)
                self._mixer = self._output = None
        else:
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
            tag = f"v2_{int(self.volume * 100)}"
            for name, notes in SOUNDS.items():
                custom = os.path.join(self.override_dir or "", name + ".wav")
                if self.override_dir and os.path.isfile(custom):
                    path = custom
                else:
                    path = os.path.join(self.folder, f"{name}_{tag}.wav")
                    if not os.path.isfile(path):
                        with open(path + ".part", "wb") as f:
                            f.write(wav(synthesize(notes, self.volume)))
                        os.replace(path + ".part", path)
                if self._mixer:
                    pcm = load_pcm(path)
                    if pcm is not None:
                        self.pcm[name] = pcm
                    else:
                        log("WARNING", "Sound not 16-bit PCM, played with winsound", path)
                self.files[name] = path
        except Exception as e:
            log("WARNING", "Could not prepare sounds", e)
        self._ready = True
        pending, self._pending = self._pending, None
        if pending:
            self.play(pending)

    def play(self, name, player=None):
        """Play `name` ("join" + player 2 -> "join2") without waiting."""
        if not self.enabled:
            return
        key = f"{name}{player}" if player else name
        if key in self.pcm:
            self._mixer.add(self.pcm[key])
            self._output.wake()
            return
        path = self.files.get(key)
        if not path:
            if not self._ready:
                self._pending = key
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

    # ------------------------------------------------------------------
    def suspend(self):
        """A game takes over: release the audio device once nothing plays."""
        if self._output:
            self._output.keep_open = False

    def resume(self):
        """Back on screen: keep the device open so no sound loses its start."""
        if self._output:
            self._output.keep_open = True
            self._output.wake()

    def shutdown(self):
        """This object is being replaced (sound settings changed): stop its output."""
        self.enabled = False
        if self._output:
            self._output.shutdown()
