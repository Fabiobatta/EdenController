"""
Core/Sound.py
Short interface sounds: a player joining, confirming, the roulette...

The sounds are synthesised (FM bells and a wooden tick, see SOUNDS) into
WAV files in the temp folder the first time the launcher runs, on a
background thread, so the launcher ships no audio files. A WAV with the same name in the
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

# ============================================================================
# THE SOUNDS
# Soft, round bells and a light wooden tap, in the spirit of a console's
# menus: gentle attacks, few overtones, quiet, short, with a little room
# around them. Every melody here is original - short arpeggios on major and
# lydian chords, nothing taken from any game.
# ============================================================================
def _hz(name):
    """Frequency of a note name: "C5", "F#6", "Bb4"."""
    letters = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    semitone = letters[name[0]] + {"#": 1, "b": -1}.get(name[1], 0)
    octave = int(name[2:] if name[1] in "#b" else name[1:])
    return 440.0 * 2 ** ((semitone - 9) / 12 + (octave - 4))


# Each player has their own note (C major pentatonic, rising), so the join
# chime tells the number without looking
_PLAYER_NOTES = ("C5", "D5", "E5", "G5", "A5", "C6", "D6", "E6")

# Instruments: (modulator ratio, index at the attack, index after it, index
# decay s, amplitude decay s, attack s, sparkle gain). FM synthesis: a sine
# whose frequency is wobbled by another sine; the wobble (index) dies
# away faster than the note, which is what makes a bell ring "mellow".
# Low indexes and soft attacks: round tones, close to a sine, no edge.
INSTRUMENTS = {
    "bell": (2.0, 0.85, 0.08, 0.045, 0.20, 0.007, 0.0),    # the main voice: a soft, round bell
    "chime": (2.0, 0.55, 0.05, 0.06, 0.40, 0.012, 0.0),    # longer and gentler: the end of a jingle
    "wood": (2.0, 0.7, 0.0, 0.010, 0.022, 0.003, 0.0),     # a light wooden tap: moving around
    "soft": (1.0, 0.45, 0.1, 0.05, 0.14, 0.010, 0.0),      # round and low: a gentle "no"
}

# name -> {"notes": [(start s, note, ring s, gain, instrument)], "level": peak, "room": reverb}
SOUNDS = {
    "move":   {"notes": [(0.0, "C6", 0.07, 1.0, "wood")], "level": 0.2, "room": False},
    "tick":   {"notes": [(0.0, "E6", 0.06, 1.0, "wood")], "level": 0.18, "room": False},
    "edge":   {"notes": [(0.0, "G4", 0.06, 1.0, "wood")], "level": 0.22, "room": False},
    "star":   {"notes": [(0.0, "E6", 0.18, 0.6, "bell"), (0.05, "G6", 0.18, 0.6, "bell"),
                         (0.1, "C7", 0.5, 0.7, "chime")], "level": 0.26},
    "select": {"notes": [(0.0, "E5", 0.3, 0.8, "bell"), (0.05, "B5", 0.45, 1.0, "bell")], "level": 0.4},
    "back":   {"notes": [(0.0, "B5", 0.28, 0.8, "bell"), (0.05, "E5", 0.4, 0.9, "bell")], "level": 0.32},
    "toggle": {"notes": [(0.0, "G5", 0.25, 0.8, "bell"), (0.04, "C6", 0.35, 1.0, "bell")], "level": 0.32},
    "leave":  {"notes": [(0.0, "C6", 0.25, 0.9, "bell"), (0.07, "A5", 0.25, 0.9, "bell"),
                         (0.14, "E5", 0.45, 1.0, "bell")], "level": 0.34},
    "error":  {"notes": [(0.0, "A4", 0.25, 1.0, "soft"), (0.09, "E4", 0.35, 1.0, "soft")], "level": 0.38},
    "launch": {"notes": [(0.0, "C5", 0.3, 0.75, "bell"), (0.07, "E5", 0.3, 0.8, "bell"),
                         (0.14, "G5", 0.3, 0.85, "bell"), (0.22, "C6", 1.0, 0.9, "chime"),
                         (0.22, "G5", 1.0, 0.45, "chime")], "level": 0.48},
    "win":    {"notes": [(0.0, "G4", 0.3, 0.8, "bell"), (0.07, "B4", 0.3, 0.8, "bell"),
                         (0.14, "D5", 0.3, 0.85, "bell"), (0.22, "G5", 1.1, 0.9, "chime"),
                         (0.22, "D5", 1.1, 0.45, "chime")], "level": 0.5},
    "trophy": {"notes": [(0.0, "F4", 0.3, 0.75, "bell"), (0.07, "A4", 0.3, 0.8, "bell"),
                         (0.14, "C5", 0.3, 0.85, "bell"), (0.21, "E5", 0.3, 0.85, "bell"),
                         (0.29, "C6", 1.2, 0.85, "chime"), (0.29, "G5", 1.2, 0.5, "chime")], "level": 0.5},
}
# Joining: one blip per pulse of the confirmation rumble, on the player's own
# note, then a small rising chime that ends on it an octave up
for _player, _name in enumerate(_PLAYER_NOTES, start=1):
    _octave_up = _name[0] + str(int(_name[1:]) + 1)
    SOUNDS[f"blip{_player}"] = {"notes": [(0.0, _name, 0.3, 1.0, "bell")], "level": 0.3}
    SOUNDS[f"join{_player}"] = {"notes": [(0.0, _name, 0.3, 0.8, "bell"),
                                          (0.06, _hz(_name) * 1.5, 0.3, 0.8, "bell"),
                                          (0.12, _octave_up, 0.7, 0.85, "chime")], "level": 0.38}

ROOM = ((0.067, 0.16), (0.109, 0.10), (0.151, 0.06))    # (delay s, gain): a small, soft room
LOWPASS = 0.3                                           # one-pole smoothing: takes the edge off the top
BRIGHT_LIMIT = 6000.0                                   # Hz: no partial above this (RATE / 2 is 11025)


def _render_note(samples, first, frequency, ring, gain, instrument):
    """Add one FM note to `samples` from index `first` (envelopes by running products: no exp per sample)."""
    ratio, index_hi, index_lo, index_tau, amp_tau, attack, sparkle = INSTRUMENTS[instrument]
    amp_tau = min(amp_tau * (ring / 0.4) ** 0.5, ring / 3) if instrument in ("bell", "chime") else amp_tau
    count = min(int(ring * RATE), len(samples) - first)
    if count <= 0:
        return
    # FM sidebands reach about frequency * (1 + ratio * (index + 1)): keep them
    # under BRIGHT_LIMIT, or high notes fold back into harsh, off-key noise
    room = max(0.0, (BRIGHT_LIMIT / frequency - 1) / ratio - 1)
    if index_hi > room:
        index_lo, index_hi = min(index_lo, room), room
    if frequency * 4 > BRIGHT_LIMIT:
        sparkle = 0.0
    w = 2 * math.pi * frequency / RATE
    wm = w * ratio
    ws = w * 4.0                                            # sparkle: a quiet partial two octaves up
    amp_k = math.exp(-1.0 / (amp_tau * RATE))
    index_k = math.exp(-1.0 / (index_tau * RATE))
    sparkle_k = math.exp(-1.0 / (0.04 * RATE))
    attack_n = max(1, int(attack * RATE))
    release_n = max(1, int(0.03 * RATE))
    amp, swing, glint = gain, index_hi - index_lo, sparkle * gain
    sin = math.sin
    for i in range(count):
        envelope = amp * (i / attack_n if i < attack_n else 1.0)
        if i > count - release_n:
            envelope *= (count - i) / release_n               # never stop on a non-zero sample
        index = index_lo + swing
        samples[first + i] += envelope * sin(w * i + index * sin(wm * i)) + glint * sin(ws * i)
        amp *= amp_k
        swing *= index_k
        glint *= sparkle_k


def synthesize(sound, volume=1.0):
    """
    16-bit mono PCM of a sound from SOUNDS: its notes rendered, a little
    room added, softened, and scaled so its peak is the sound's level.
    """
    if isinstance(sound, list):                         # a bare list of notes
        sound = {"notes": sound}
    notes = sound["notes"]
    room = sound.get("room", True)
    tail = (max(d for d, _ in ROOM) * 3 if room else 0.0) + TAIL_S
    length = max(start + ring for start, _, ring, _, *_ in notes) + tail
    samples = [0.0] * int(length * RATE)
    for start, note, ring, gain, *rest in notes:
        frequency = _hz(note) if isinstance(note, str) else float(note)
        _render_note(samples, int(start * RATE), frequency, ring, gain, rest[0] if rest else "bell")
    if room:
        for delay, gain in ROOM:                        # feedback echoes, each quieter than the last
            d = int(delay * RATE)
            for i in range(d, len(samples)):
                samples[i] += gain * samples[i - d]
    smooth, previous = [], 0.0
    for value in samples:
        previous += LOWPASS * (value - previous)
        smooth.append(previous)
    peak = max(1e-9, max(abs(v) for v in smooth))
    scale = 32767 * sound.get("level", 0.6) * max(0.0, min(1.0, volume)) / peak
    return array("h", (int(max(-32767, min(32767, v * scale))) for v in smooth)).tobytes()


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
            tag = f"v4_{int(self.volume * 100)}"
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
