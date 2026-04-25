"""
Generate TTS audio from a story text file using Gemini 2.5 Flash TTS.

Usage:
    python generate_tts.py <story_file> [output_dir] [voice_name]

Example:
    python generate_tts.py story.txt . Puck
"""

import os
import sys
import wave
import json
import subprocess


def read_story(filepath: str) -> str:
    """Read the story text from file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read().strip()


def generate_tts_audio(client, text: str, voice_name: str = "Puck") -> bytes:
    """Generate TTS audio for a single text chunk using Gemini."""
    from google.genai import types

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name,
                    )
                )
            ),
        ),
    )

    data = response.candidates[0].content.parts[0].inline_data.data
    return data


def save_wav(pcm_data: bytes, filepath: str, sample_rate: int = 24000) -> None:
    """Save raw PCM data as a WAV file."""
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def generate_silence_pcm(duration_seconds: float, sample_rate: int = 24000) -> bytes:
    """Generate silence as raw PCM data."""
    num_samples = int(sample_rate * duration_seconds)
    return b"\x00\x00" * num_samples


def convert_wav_to_mp3(wav_path: str, mp3_path: str) -> None:
    """Convert WAV file to MP3 using ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            wav_path,
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "2",
            mp3_path,
        ],
        check=True,
        capture_output=True,
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_tts.py <story_file> [output_dir] [voice_name]")
        print()
        print("Available voices: Puck, Kore, Charon, Zephyr, Fenrir, Aoede,")
        print("                  Leda, Orus, Callirrhoe, Autonoe, Enceladus")
        sys.exit(1)

    story_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    voice_name = sys.argv[3] if len(sys.argv) > 3 else "Puck"

    # Check API key
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY environment variable not set")
        print("   Export it: export GEMINI_API_KEY=your_api_key")
        sys.exit(1)

    # Check ffmpeg
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except FileNotFoundError:
        print("❌ Error: ffmpeg not found. Install it: brew install ffmpeg")
        sys.exit(1)

    # Initialize Gemini client
    from google import genai

    client = genai.Client(api_key=api_key)

    # Read story
    text = read_story(story_file)

    print(f"📖 Story file: {story_file}")
    print(f"🔊 Voice: {voice_name}")
    print(f"📝 Text length: {len(text)} chars")
    print()

    # Generate TTS for the full text in a single request
    print("  🎙️  Generating TTS audio...")

    try:
        pcm_data = generate_tts_audio(client, text, voice_name)
        print(f"      ✅ Done ({len(pcm_data)} bytes)")
    except Exception as e:
        print(f"      ❌ Error: {e}")
        sys.exit(1)

    # Add intro/outro silence
    silence_intro = generate_silence_pcm(1.5)
    silence_outro = generate_silence_pcm(1.5)
    all_pcm = silence_intro + pcm_data + silence_outro

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Save as WAV first
    wav_path = os.path.join(output_dir, "narration.wav")
    mp3_path = os.path.join(output_dir, "narration.mp3")

    print()
    print(f"💾 Saving WAV: {wav_path}")
    save_wav(all_pcm, wav_path)

    # Calculate duration
    duration_seconds = len(all_pcm) / (24000 * 2)  # 24kHz, 16-bit (2 bytes per sample)
    minutes = int(duration_seconds // 60)
    seconds = int(duration_seconds % 60)
    print(f"⏱️  Audio duration: {minutes}m {seconds}s")

    # Convert to MP3
    print(f"🔄 Converting to MP3: {mp3_path}")
    convert_wav_to_mp3(wav_path, mp3_path)

    # Clean up WAV
    os.remove(wav_path)

    # Save audio info JSON (for Remotion to read duration)
    info_path = os.path.join(output_dir, "audio_info.json")
    with open(info_path, "w") as f:
        json.dump({"duration": round(duration_seconds, 3)}, f)

    print()
    print(f"✅ Audio saved to: {mp3_path}")
    print(f"📊 Audio info: {info_path}")


def run_tts_file(story_file, output_dir=None, voice_name: str = "Puck") -> dict:
    """
    Programmatic entry point — no sys.exit.
    Returns {"mp3": str, "info": str, "duration": float}.
    Raises RuntimeError on failure.
    """
    from pathlib import Path

    story_file = Path(story_file)
    out_dir = Path(output_dir) if output_dir else story_file.parent

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable not set")

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found — install it or add it to PATH")

    from google import genai
    client = genai.Client(api_key=api_key)

    text = read_story(str(story_file))
    pcm_data = generate_tts_audio(client, text, voice_name)

    silence = generate_silence_pcm(1.5)
    all_pcm = silence + pcm_data + silence

    os.makedirs(out_dir, exist_ok=True)
    wav_path = str(out_dir / "narration.wav")
    mp3_path = str(out_dir / "narration.mp3")
    info_path = str(out_dir / "audio_info.json")

    save_wav(all_pcm, wav_path)
    convert_wav_to_mp3(wav_path, mp3_path)
    os.remove(wav_path)

    duration = len(all_pcm) / (24000 * 2)
    with open(info_path, "w") as f:
        json.dump({"duration": round(duration, 3)}, f)

    return {"mp3": mp3_path, "info": info_path, "duration": round(duration, 3)}


if __name__ == "__main__":
    main()
