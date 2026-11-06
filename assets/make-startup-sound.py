"""Synthesizes assets/startup.m4a, the startup chime, timed to ui/splash.html.

No leading silence: a soft sub thump and a click land on the first sample, under a quick rising E major arpeggio of
glassy plucks (E5, G#5, B5) that tracks the molecule drawing in. As the amine atom pops (HIT, 0.45 s, where the
splash flashes) a bright E6/B6 bell rings out and fades. About 1.3 s in all.
Pure standard library; afconvert (macOS) encodes the result to AAC.
Run: /usr/bin/python3 assets/make-startup-sound.py
"""
import math
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import wave

RATE = 44100
LENGTH = 1.3
HIT = 0.45  # the amine atom's pop and the flash in ui/splash.html
N = int(RATE * LENGTH)
left = [0.0] * N
right = [0.0] * N


def add(signal, start, pan):
    """Mixes a mono signal in at `start` seconds; pan -1 (left) to 1 (right), equal power."""
    angle = (pan + 1) * math.pi / 4
    gl, gr = math.cos(angle), math.sin(angle)
    offset = int(start * RATE)
    for i, value in enumerate(signal):
        if offset + i >= N:
            break
        left[offset + i] += value * gl
        right[offset + i] += value * gr


def thump(duration, gain):
    """Sub kick: a sine falling from 150 Hz to 48 Hz with a fast decay, so it punches without booming."""
    out, phase = [], 0.0
    for i in range(int(duration * RATE)):
        t = i / RATE
        freq = 48 + 102 * math.exp(-t / 0.03)
        phase += 2 * math.pi * freq / RATE
        out.append(gain * min(1.0, t / 0.001) * math.exp(-t / 0.09) * math.sin(phase))
    return out


def click(duration, gain):
    """The transient: high-passed noise that is gone in a few milliseconds."""
    rng = random.Random(1108)
    out, last = [], 0.0
    for i in range(int(duration * RATE)):
        t = i / RATE
        noise = rng.uniform(-1, 1)
        out.append(gain * math.exp(-t / 0.004) * (noise - last))
        last = noise
    return out


def pluck(freq, duration, gain, decay):
    """A glassy pluck: a light FM tone whose modulation (and so its brightness) dies away faster than the note."""
    out = []
    for i in range(int(duration * RATE)):
        t = i / RATE
        index = 1.8 * math.exp(-t / 0.05)
        env = min(1.0, t / 0.002) * math.exp(-t / decay)
        value = math.sin(2 * math.pi * freq * t + index * math.sin(2 * math.pi * freq * 2 * t))
        value += 0.25 * math.sin(2 * math.pi * freq * 2 * t) * math.exp(-t / 0.08)
        out.append(gain * env * value)
    return out


def bell(freq, duration, gain):
    """A bright bell: inharmonic partials, each decaying at its own rate, with a slow shimmer."""
    out = []
    partials = ((1.0, 1.0, 0.7), (2.0, 0.45, 0.4), (2.76, 0.3, 0.25), (5.4, 0.12, 0.12))
    for i in range(int(duration * RATE)):
        t = i / RATE
        attack = min(1.0, t / 0.003)
        shimmer = 1 + 0.15 * math.sin(2 * math.pi * 6 * t)
        value = sum(weight * math.exp(-t / tau) * math.sin(2 * math.pi * freq * ratio * t) for ratio, weight, tau in partials)
        out.append(gain * attack * shimmer * value)
    return out


E5, GS5, B5, E6, B6 = 659.26, 830.61, 987.77, 1318.51, 1975.53
add(thump(0.35, 0.9), 0, 0)
add(click(0.03, 0.35), 0, 0)
# The arpeggio climbs left to right as the molecule draws in.
for start, freq, pan in ((0.0, E5, -0.45), (0.075, GS5, 0), (0.15, B5, 0.45)):
    add(pluck(freq, 0.6, 0.28, 0.16), start, pan)
add(bell(E6, LENGTH - HIT, 0.22), HIT, -0.25)
add(bell(B6, LENGTH - HIT, 0.12), HIT, 0.3)
add(click(0.02, 0.15), HIT, 0.2)

# Short Schroeder reverb, with different comb lengths per side for width.
def reverb(dry, combs, mix=0.22):
    wet = [0.0] * N
    for delay, feedback in combs:
        buf = [0.0] * delay
        for i in range(N):
            j = i % delay
            out = buf[j]
            buf[j] = dry[i] + out * feedback
            wet[i] += out / len(combs)
    for delay in (225, 556):
        buf = [0.0] * delay
        for i in range(N):
            j = i % delay
            out = buf[j] - 0.5 * wet[i]
            buf[j] = wet[i] + 0.5 * out
            wet[i] = out
    return [d + mix * w for d, w in zip(dry, wet)]

left = reverb(left, ((1116, 0.7), (1188, 0.69), (1277, 0.7), (1356, 0.68)))
right = reverb(right, ((1139, 0.7), (1211, 0.69), (1300, 0.7), (1379, 0.68)))

peak = max(max(abs(v) for v in left), max(abs(v) for v in right))
scale = 0.7 / peak  # -3 dBFS
fade = int(0.3 * RATE)
frames = bytearray()
for i in range(N):
    tail = min(1.0, (N - i) / fade)
    frames += struct.pack('<hh', int(32767 * left[i] * scale * tail), int(32767 * right[i] * scale * tail))

target = Path(__file__).with_name('startup.m4a')
with tempfile.TemporaryDirectory() as work:
    raw = Path(work, 'startup.wav')
    with wave.open(str(raw), 'wb') as out:
        out.setnchannels(2); out.setsampwidth(2); out.setframerate(RATE); out.writeframes(bytes(frames))
    subprocess.run(['afconvert', '-f', 'm4af', '-d', 'aac', '-b', '128000', str(raw), str(target)], check=True)
print('Wrote', target)
