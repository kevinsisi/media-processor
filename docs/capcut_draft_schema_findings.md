# CapCut/JianyingPro Draft Schema — Findings (mp_sample_001)

| Field | Value |
|-------|-------|
| Date captured | (PENDING) |
| Source app | (剪映 / CapCut Pro) |
| Version | (PENDING — record exact version her Mac uses) |
| Platform | macOS (girlfriend's machine) |

## Top-level structure

(Paste output from `parse_sample.py` here once sample is captured.)

## Track structure

(Notes on how video / audio / text tracks differ.)

## Materials section

(What goes in `materials`? Links between tracks and materials.)

## Position / scale keyframes

(Where do reframe keyframes live? Field names? Time units?)

## Open questions

- [ ] Are start/end times in microseconds or milliseconds?
- [ ] Is asset path absolute or relative? How are asset moves handled?
- [ ] Where do transitions live? Per-segment or separate track?
- [ ] How are captions stored — embedded in text track or separate file?
