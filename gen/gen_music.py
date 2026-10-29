"""Make one short music clip or sound effect with a local MusicGen model. Run by generate_audio.

Uses MusicGen through the transformers library (no audiocraft, so it works with a current torch build).
First run downloads facebook/musicgen-small (~1.2 GB).
"""

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--model", default="facebook/musicgen-small")
    a = ap.parse_args()

    import torch
    import soundfile as sf
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(a.model)
    model = MusicgenForConditionalGeneration.from_pretrained(a.model).to(device)

    inputs = processor(text=[a.prompt], padding=True, return_tensors="pt").to(device)
    sample_rate = model.config.audio_encoder.sampling_rate
    max_new_tokens = int(max(a.seconds, 1.0) * 50)  # MusicGen makes roughly 50 tokens per second
    with torch.no_grad():
        audio = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=True, guidance_scale=3.0)

    wav = audio[0, 0].cpu().numpy()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    sf.write(a.out, wav, sample_rate)
    print(f"OK {a.out} ({len(wav) / sample_rate:.1f}s @ {sample_rate} Hz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
