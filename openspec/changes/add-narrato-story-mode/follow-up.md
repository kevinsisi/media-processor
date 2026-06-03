## Deferred Follow-Up Scope

### TTS Narration Audio

TTS narration audio is intentionally outside this first Story/Narrato mode slice. A follow-up change should define provider selection, voice presets, generated-audio storage, real audio duration measurement, BGM/original/narration mixing, and narration-aware timeline extension before enabling rendered narration audio.

### Sampled-Frame Story Visual Analysis

The first implementation can generate StoryScript from transcript/subtitle text without visual context. A follow-up enhancement can add sampled-frame descriptions to the StoryScript prompt, cache those descriptions, and record `used_visual_context=true` when included.

### GPU-Heavy Local Analysis

Local Whisper, YOLO tracking, emotion detection, Smart Camera, stabilization, and MusicGen remain optional enhancement paths. They can improve visual matching, framing, and sound design, but they must not be blockers for core Story/Narrato script generation when usable text inputs already exist.
