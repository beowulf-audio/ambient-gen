#!/usr/bin/env python3
"""
Analyze the current GeneralUser GS mix to show perceived loudness (LUFS) of each instrument.
"""

import os
import sys
import subprocess
import tempfile
import re
from pathlib import Path

# Add ambient_gen to path
sys.path.insert(0, str(Path(__file__).parent))

from ambient_gen.tui import (
    render_channel_to_audio, apply_reverb, apply_delay,
    apply_warm_overdrive, apply_chorus, SOUNDFONT_PROFILES, SCALE_MAP,
    build_scale, pick_notes, ROOT_OPTIONS
)
from ambient_gen.soundfont_manager import SoundfontManager
from mido import MidiFile, MidiTrack, MetaMessage, Message, bpm2tempo
import numpy as np
import random
import wave
from static_ffmpeg import run

# Ensure ffmpeg is available
ffmpeg_path, _ = run.get_or_fetch_platform_executables_else_raise()

# Test composition parameters
TEST_TEMPO = 48
TEST_BARS = 12
TEST_SCALE = "Hirajoshi"
SAMPLE_RATE = 44100

# Channel mapping
CHANNEL_MAP = {
    0: 'pad',
    1: 'flute',
    2: 'vibraphone',
    3: 'strings',
    4: 'music_box'
}

def create_test_midi(output_path, profile):
    """Create a test MIDI file with all instruments enabled."""
    root = random.choice(ROOT_OPTIONS)
    scale = build_scale(root, TEST_SCALE)
    chords = [sorted(random.sample(scale, 3)) for _ in range(TEST_BARS)]
    melody_notes = pick_notes(scale, 16, [12, 24])
    counter_notes = pick_notes(scale, 12, [12, 24])
    fx_notes = pick_notes(scale, 60, [36, 48, 60])
    drone_note = root
    bar_ticks = 1920
    full_ticks = bar_ticks * TEST_BARS

    mid = MidiFile(ticks_per_beat=480)
    tempo_track = MidiTrack()
    tempo_track.append(MetaMessage('set_tempo', tempo=bpm2tempo(TEST_TEMPO)))
    tempo_track.append(MetaMessage('time_signature', numerator=4, denominator=4))
    mid.tracks.append(tempo_track)

    def make_track(channel, program, pan, reverb, chorus, name):
        t = MidiTrack()
        t.append(MetaMessage('track_name', name=name, time=0))
        t.append(Message('program_change', program=program, channel=channel, time=0))
        t.append(Message('control_change', control=91, value=reverb, channel=channel, time=0))
        t.append(Message('control_change', control=93, value=chorus, channel=channel, time=0))
        t.append(Message('control_change', control=10, value=pan, channel=channel, time=0))
        return t

    # Create all tracks
    pad = make_track(0, profile['instruments']['pad'], 64, 127, 0, "Pad")
    melody = make_track(1, profile['instruments']['flute'], 32, 80, 90, "Flute")
    counter = make_track(2, profile['instruments']['vibraphone'], 96, 70, 100, "Vibraphone")
    drone = make_track(3, profile['instruments']['strings'], 64, 127, 0, "Strings")
    fx = make_track(4, profile['instruments']['music_box'], 64, 100, 60, "Music Box")

    mid.tracks.extend([pad, melody, counter, drone, fx])

    # Add pad notes
    for i in range(TEST_BARS):
        chord = chords[i]
        delay = 0
        for note in chord:
            pad.append(Message('note_on', note=note, velocity=60, time=delay, channel=0))
            delay = 0
        for note in chord:
            pad.append(Message('note_on', note=note - 12, velocity=35, time=0, channel=0))
        all_notes = chord + [note - 12 for note in chord]
        for idx, note in enumerate(all_notes):
            pad.append(Message('note_off', note=note, velocity=60, time=bar_ticks if idx == 0 else 0, channel=0))

    # Add drone
    drone.append(Message('note_on', note=drone_note, velocity=35, time=0, channel=3))
    drone.append(Message('note_off', note=drone_note, velocity=35, time=full_ticks, channel=3))

    # Add melody
    absolute_time = 0
    time_acc = 0
    for note in melody_notes:
        if absolute_time + time_acc + 480 > full_ticks:
            break
        vel = random.randint(45, 70)
        melody.append(Message('note_on', note=note, velocity=vel, time=time_acc, channel=1))
        melody.append(Message('note_off', note=note, velocity=vel, time=480, channel=1))
        absolute_time += time_acc + 480
        time_acc = random.choice([960, 1440, 1920, 2400])

    # Add counter melody
    absolute_time = 0
    time_acc = 0
    for note in counter_notes:
        if absolute_time + time_acc + 600 > full_ticks:
            break
        vel = random.randint(40, 60)
        counter.append(Message('note_on', note=note, velocity=vel, time=time_acc, channel=2))
        counter.append(Message('note_off', note=note, velocity=vel, time=600, channel=2))
        absolute_time += time_acc + 600
        time_acc = random.choice([1920, 2880, 3840])

    # Add music box
    absolute_time = 0
    fx_time = 0
    for note in fx_notes:
        if absolute_time + fx_time + 240 > full_ticks:
            break
        vel = random.randint(35, 65)
        pan = random.randint(0, 127)
        fx.append(Message('control_change', control=10, value=pan, channel=4, time=0))
        fx.append(Message('note_on', note=note, velocity=vel, time=fx_time, channel=4))
        fx.append(Message('note_off', note=note, velocity=vel, time=240, channel=4))
        absolute_time += fx_time + 240
        fx_time = random.choice([240, 480, 720, 960])

    mid.save(output_path)


def get_total_samples(midi_path):
    """Calculate total samples needed for MIDI file."""
    mid = MidiFile(midi_path)
    total_ticks = 0
    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
        track_ticks = sum(m.time for m in track if hasattr(m, 'time'))
        total_ticks = max(total_ticks, track_ticks)

    ticks_per_beat = mid.ticks_per_beat
    tempo_sec = tempo / 1000000.0
    total_seconds = (total_ticks / ticks_per_beat) * tempo_sec
    return int(total_seconds * SAMPLE_RATE) + SAMPLE_RATE


def render_instrument_with_effects(midi_path, soundfont_path, channel, total_samples, enable_effects=True):
    """Render a single instrument with effects applied (matching production pipeline)."""
    # Render raw audio (convert paths to strings)
    audio = render_channel_to_audio(str(midi_path), str(soundfont_path), channel, total_samples, SAMPLE_RATE)

    # Calculate delay times based on tempo
    beat_duration = 60.0 / TEST_TEMPO
    eighth_note_delay = beat_duration / 2
    half_note_delay = beat_duration * 2

    if enable_effects:
        # Apply same effects as production (without volume multiplier)
        if channel == 0:  # Pad
            audio = apply_reverb(audio, SAMPLE_RATE, room_size=0.85, damping=0.78, wet=0.92)
        elif channel == 1:  # Flute
            audio = apply_reverb(audio, SAMPLE_RATE, room_size=0.85, damping=0.78, wet=0.92)
        elif channel == 2:  # Vibraphone
            audio = apply_reverb(audio, SAMPLE_RATE, room_size=0.65, damping=0.6, wet=0.65)
            audio = apply_delay(audio, SAMPLE_RATE, half_note_delay, feedback=0.65, wet=0.5)
        elif channel == 3:  # Drone/Strings
            audio = apply_warm_overdrive(audio, drive=9.0, mix=0.95)
            audio = apply_chorus(audio, SAMPLE_RATE, rate=0.5, depth=0.002, mix=0.25)
        elif channel == 4:  # Music Box
            audio = apply_delay(audio, SAMPLE_RATE, eighth_note_delay, feedback=0.75, wet=0.6)
            audio = apply_chorus(audio, SAMPLE_RATE, rate=0.3, depth=0.004, mix=0.3)

    return audio


def save_wav(audio, output_path):
    """Save mono audio array to WAV file."""
    with wave.open(str(output_path), 'w') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(SAMPLE_RATE)
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        wav_file.writeframes(audio_int16.tobytes())


def measure_lufs(wav_path):
    """Use ffmpeg loudnorm filter to measure integrated LUFS."""
    result = subprocess.run(
        [ffmpeg_path, '-i', str(wav_path), '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
        capture_output=True,
        text=True
    )

    # Parse JSON output for integrated loudness
    output = result.stderr + result.stdout
    match = re.search(r'"input_i"\s*:\s*"([-\d.]+)"', output)
    if match:
        return float(match.group(1))
    else:
        raise RuntimeError(f"Could not find input_i in ffmpeg output:\n{output}")


def measure_peak_volume(wav_path):
    """Use ffmpeg volumedetect to measure peak volume in dB."""
    result = subprocess.run(
        [ffmpeg_path, '-i', str(wav_path), '-af', 'volumedetect', '-f', 'null', '-'],
        capture_output=True,
        text=True
    )

    # Parse output for max_volume (ffmpeg outputs to stderr)
    output = result.stderr + result.stdout
    match = re.search(r'max_volume:\s+([-\d.]+)\s+dB', output)
    if match:
        return float(match.group(1))
    else:
        raise RuntimeError(f"Could not find max_volume in ffmpeg output:\n{output}")


def main():
    print("="*70)
    print("GENERALUSER GS MIX ANALYSIS")
    print("="*70)

    # Initialize soundfont manager
    manager = SoundfontManager()
    soundfonts = {}
    for font in manager.available_fonts:
        soundfonts[font['name']] = font['path']

    if 'GeneralUser GS' not in soundfonts:
        print("ERROR: GeneralUser GS soundfont not found!")
        return 1

    soundfont_path = soundfonts['GeneralUser GS']
    profile = SOUNDFONT_PROFILES['GeneralUser GS']

    print(f"\nSoundfont: {soundfont_path}")
    print(f"Test composition: {TEST_BARS} bars at {TEST_TEMPO} BPM, scale: {TEST_SCALE}")

    # Use fixed seed for reproducible results
    random.seed(42)

    # Create temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Generate test MIDI
        print(f"\nGenerating test MIDI...")
        midi_path = tmpdir_path / "test.mid"
        create_test_midi(midi_path, profile)
        total_samples = get_total_samples(midi_path)

        # Analyze with effects
        print("\n" + "="*70)
        print("WITH EFFECTS (current production settings)")
        print("="*70)
        print(f"\nCurrent volume settings:")
        for inst_name, vol in profile['volumes_with_effects'].items():
            print(f"  {inst_name:15s}: {vol}")

        print(f"\n{'Instrument':<15} {'Peak (dB)':<12} {'LUFS':<12} {'Volume Mult':<12} {'Effective LUFS'}")
        print("-" * 70)

        results_with = {}
        for channel, instrument_name in CHANNEL_MAP.items():
            # Render with effects, no volume multiplier
            audio = render_instrument_with_effects(
                midi_path, soundfont_path, channel, total_samples, enable_effects=True
            )

            # Save to temp WAV
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name

            try:
                save_wav(audio, tmp_path)
                peak_db = measure_peak_volume(tmp_path)
                lufs = measure_lufs(tmp_path)
                volume_mult = profile['volumes_with_effects'][instrument_name]

                # Effective LUFS after volume multiplier (approximation: LUFS + 20*log10(volume))
                volume_db = 20 * np.log10(volume_mult) if volume_mult > 0 else -float('inf')
                effective_lufs = lufs + volume_db

                results_with[instrument_name] = {
                    'peak': peak_db,
                    'lufs': lufs,
                    'volume_mult': volume_mult,
                    'effective_lufs': effective_lufs
                }

                print(f"{instrument_name:<15} {peak_db:>10.1f}  {lufs:>10.1f}  {volume_mult:>10.2f}  {effective_lufs:>10.1f}")
            finally:
                os.unlink(tmp_path)

        # Analyze without effects
        print("\n" + "="*70)
        print("WITHOUT EFFECTS")
        print("="*70)
        print(f"\nCurrent volume settings:")
        for inst_name, vol in profile['volumes_no_effects'].items():
            print(f"  {inst_name:15s}: {vol}")

        print(f"\n{'Instrument':<15} {'Peak (dB)':<12} {'LUFS':<12} {'Volume Mult':<12} {'Effective LUFS'}")
        print("-" * 70)

        results_no = {}
        for channel, instrument_name in CHANNEL_MAP.items():
            # Render without effects, no volume multiplier
            audio = render_instrument_with_effects(
                midi_path, soundfont_path, channel, total_samples, enable_effects=False
            )

            # Save to temp WAV
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name

            try:
                save_wav(audio, tmp_path)
                peak_db = measure_peak_volume(tmp_path)
                lufs = measure_lufs(tmp_path)
                volume_mult = profile['volumes_no_effects'][instrument_name]

                # Effective LUFS after volume multiplier
                volume_db = 20 * np.log10(volume_mult) if volume_mult > 0 else -float('inf')
                effective_lufs = lufs + volume_db

                results_no[instrument_name] = {
                    'peak': peak_db,
                    'lufs': lufs,
                    'volume_mult': volume_mult,
                    'effective_lufs': effective_lufs
                }

                print(f"{instrument_name:<15} {peak_db:>10.1f}  {lufs:>10.1f}  {volume_mult:>10.2f}  {effective_lufs:>10.1f}")
            finally:
                os.unlink(tmp_path)

        # Summary
        print("\n" + "="*70)
        print("SUMMARY")
        print("="*70)
        print("\nEffective perceived loudness (LUFS) in final mix:")
        print("\nWITH EFFECTS:")
        sorted_with = sorted(results_with.items(), key=lambda x: x[1]['effective_lufs'], reverse=True)
        for inst_name, data in sorted_with:
            print(f"  {inst_name:15s}: {data['effective_lufs']:>6.1f} LUFS")

        print("\nWITHOUT EFFECTS:")
        sorted_no = sorted(results_no.items(), key=lambda x: x[1]['effective_lufs'], reverse=True)
        for inst_name, data in sorted_no:
            print(f"  {inst_name:15s}: {data['effective_lufs']:>6.1f} LUFS")

    return 0


if __name__ == '__main__':
    sys.exit(main())
