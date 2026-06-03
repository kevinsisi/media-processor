## ADDED Requirements

### Requirement: Story mode generates narration audio from StoryScript
The system SHALL generate TTS narration audio for StoryScript items whose `audio_intent` is `narration` or `narration_with_original` when narration audio is enabled and a TTS provider is configured.

#### Scenario: Narration item generates audio
- **WHEN** a story-mode render includes a StoryScript item with `audio_intent=narration` and narration audio is enabled
- **THEN** the system generates a narration audio artifact for that item before final audio mixing

#### Scenario: Original-audio item skips TTS
- **WHEN** a StoryScript item has `audio_intent=original`
- **THEN** the system MUST NOT generate narration audio for that item and MUST preserve the source-audio intent for the segment

#### Scenario: TTS is unavailable
- **WHEN** no TTS provider is configured or narration audio is disabled
- **THEN** story-mode rendering continues with narration subtitles and MUST NOT fail solely because TTS is unavailable

### Requirement: Narration artifacts are durable and invalidatable
The system SHALL persist generated narration audio artifacts with provider, model, voice, text identity, source StoryScript item identity, status, error, file path, and measured duration metadata.

#### Scenario: Artifact can be reused
- **WHEN** the StoryScript item text, voice, provider, model, and item identity match an existing successful narration artifact
- **THEN** the system may reuse the existing artifact instead of regenerating the same audio

#### Scenario: Script text changes
- **WHEN** a StoryScript item's narration text changes after an artifact was generated
- **THEN** the system treats the existing artifact as stale and generates or requests a new artifact before using narration audio

#### Scenario: TTS generation fails
- **WHEN** the TTS provider fails for an item
- **THEN** the artifact records a failed status and actionable error, and the system either falls back to subtitle-only rendering or fails the narration step according to the user-selected mode

### Requirement: Actual audio duration controls narration timing
The system SHALL probe generated narration audio files and use measured audio duration as the authoritative narration duration for story-mode timeline planning.

#### Scenario: Narration is longer than source range
- **WHEN** measured narration audio duration is longer than the StoryScript item's source range duration
- **THEN** the generated story-mode draft timeline extends the visual segment duration to at least the narration duration instead of clipping the narration audio

#### Scenario: Narration is shorter than source range
- **WHEN** measured narration audio duration is shorter than or equal to the source range duration
- **THEN** the generated story-mode draft timeline may keep the source range duration and MUST keep narration, subtitles, and final mix synchronized

### Requirement: Extended visuals do not produce black tails
The system SHALL render visual output for narration-extended segments without producing black frames at the tail of the segment.

#### Scenario: Source range is shorter than narration
- **WHEN** a visual segment must extend beyond its selected source range to match narration duration
- **THEN** the renderer emits valid video frames for the extended duration by using a safe extension strategy such as freeze-frame or looped visual content

### Requirement: Audio intent controls narration and original-audio mixing
The system SHALL mix narration audio, original source audio, and BGM according to each StoryScript item's `audio_intent`.

#### Scenario: Narration replaces original audio
- **WHEN** a StoryScript item has `audio_intent=narration`
- **THEN** the segment uses generated narration as the primary voice track and mutes or strongly ducks original source audio for that segment

#### Scenario: Narration coexists with original audio
- **WHEN** a StoryScript item has `audio_intent=narration_with_original`
- **THEN** the segment includes generated narration and retains original source audio underneath it at a ducked level

#### Scenario: Original audio remains primary
- **WHEN** a StoryScript item has `audio_intent=original`
- **THEN** the segment keeps source audio as the primary voice/audio content and does not add generated narration
