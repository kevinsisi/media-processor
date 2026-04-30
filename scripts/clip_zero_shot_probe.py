"""Run CLIP zero-shot classification against carsmeet screenshots.

Outputs a per-file table of (expected_tag, predicted_tag, score) and a
summary of accuracy. Intended as a one-off accuracy probe for the
profile tag list — not production code.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import torch
from open_clip import create_model_and_transforms, get_tokenizer
from PIL import Image

# Tags pulled from carsmeet-luxury.yaml plus prompt phrasing.
PROMPTS = {
    "logo_close_up": "a close-up of a luxury car emblem or hood ornament",
    "integral_hero_shot": "a full luxury car at a 45 degree front angle in a showroom",
    "body_line_pan": "a close-up tracking shot of luxury car body curves and reflections",
    "light_reflection": "showroom lights reflecting off a glossy car body",
    "wheel_caliper": "a close-up of an alloy car wheel and brake caliper",
    "interior_leather": "the inside of a luxury car showing diamond-stitched leather seats",
    "dashboard": "a close-up of a luxury car dashboard and steering wheel",
    "star_ceiling": "starlight headliner or constellation roof of a Rolls-Royce",
    "exhaust_pipe": "a close-up of a car exhaust pipe and rear bumper",
    "stranger_face": "a candid photograph of a person's face",
    "parking_lot_other_car": "an outdoor parking lot with multiple unrelated cars",
}

LABELS = list(PROMPTS.keys())
TEXT_PROMPTS = list(PROMPTS.values())


def main(samples_dir: Path, labels_csv: Path) -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    model, _, preprocess = create_model_and_transforms(
        "ViT-L-14", pretrained="laion2b_s32b_b82k"
    )
    model = model.to(device).eval()
    tokenizer = get_tokenizer("ViT-L-14")

    text_tokens = tokenizer(TEXT_PROMPTS).to(device)
    with torch.no_grad():
        text_features = model.encode_text(text_tokens)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    # Load expected labels
    expected: dict[str, str] = {}
    with labels_csv.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            expected[row["file"]] = row["expected_tag"]

    correct = 0
    total = 0
    rows: list[tuple[str, str, str, float]] = []
    for img_path in sorted(samples_dir.glob("*.jpg")):
        if img_path.name == "labels.csv":
            continue
        img = preprocess(Image.open(img_path).convert("RGB")).unsqueeze(0).to(device)
        with torch.no_grad():
            image_features = model.encode_image(img)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            sims = (image_features @ text_features.T).squeeze(0)
            probs = sims.softmax(dim=-1)
            top_idx = int(probs.argmax().item())
            top_label = LABELS[top_idx]
            top_score = float(probs[top_idx].item())

        exp = expected.get(img_path.name, "?")
        ok = "OK" if exp == top_label else "MISS"
        if exp == top_label:
            correct += 1
        total += 1
        rows.append((img_path.name, exp, top_label, top_score))
        print(
            f"{img_path.name:>10}  expected={exp:>22}  "
            f"predicted={top_label:>22}  score={top_score:.2f}  {ok}"
        )

    accuracy = correct / total if total else 0.0
    print()
    print(f"Accuracy: {correct}/{total} = {accuracy:.1%}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=Path, default=Path("samples/carsmeet_screenshots"))
    p.add_argument(
        "--labels", type=Path, default=Path("samples/carsmeet_screenshots/labels.csv")
    )
    args = p.parse_args()
    main(args.samples, args.labels)
