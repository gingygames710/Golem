"""Make one image with a local Stable Diffusion model (SD 1.5). Run by agent.py's generate_image tool.

Kept as a separate process so torch/diffusers stay out of the agent and the GPU memory is freed on exit.
First run downloads the model (~2 GB) from Hugging Face into your cache.
"""

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--negative", default="")
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--transparent", action="store_true", help="Cut out the background (RGBA)")
    ap.add_argument("--model", default="stable-diffusion-v1-5/stable-diffusion-v1-5")
    a = ap.parse_args()

    import torch
    from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    # safety_checker is off: it blanks out plenty of harmless game art and pulls an extra model.
    pipe = StableDiffusionPipeline.from_pretrained(a.model, torch_dtype=dtype, safety_checker=None)
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    pipe = pipe.to(device)
    if device == "cuda":
        pipe.enable_attention_slicing()  # keeps VRAM low so it fits beside the LLM

    prompt = a.prompt
    if a.transparent:
        # A plain, even background removes cleanly; nudge the model toward one.
        prompt += ", centered, full object in frame, isolated on a plain flat white background"

    generator = torch.Generator(device=device).manual_seed(a.seed) if a.seed >= 0 else None
    image = pipe(prompt, negative_prompt=a.negative or None, num_inference_steps=a.steps,
                 width=a.width, height=a.height, generator=generator).images[0]

    if a.transparent:
        try:
            from rembg import remove
            image = remove(image)  # returns an RGBA image with the background removed
        except Exception as e:  # noqa: BLE001 - still save the opaque image if removal fails
            print(f"[warn] background removal failed ({e}); saving opaque image", file=sys.stderr)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    image.save(a.out)
    print(f"OK {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
