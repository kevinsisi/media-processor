# CLIP Zero-shot Probe — Findings

| Field | Value |
|-------|-------|
| Date | (PENDING) |
| Model | open_clip ViT-L-14 / laion2b_s32b_b82k |
| Sample size | 30 carsmeet screenshots |
| Overall accuracy | (PENDING) |

## Per-tag accuracy

| Tag | Correct / Total | Notes |
|-----|-----------------|-------|
| logo_close_up | _ / _ | |
| integral_hero_shot | _ / _ | |
| body_line_pan | _ / _ | |
| light_reflection | _ / _ | |
| wheel_caliper | _ / _ | |
| interior_leather | _ / _ | |
| dashboard | _ / _ | |
| star_ceiling | _ / _ | |
| exhaust_pipe | _ / _ | |
| stranger_face | _ / _ | |
| parking_lot_other_car | _ / _ | |

## Conclusions

- Tags with accuracy ≥ 70%: usable as-is for MVP
- Tags with accuracy 40–70%: refine prompt phrasing in stage 1
- Tags with accuracy < 40%: defer to Phase β fine-tune; rely on review-driven labels

## Prompt revisions tried

(Document any prompt rewrites you tested.)
