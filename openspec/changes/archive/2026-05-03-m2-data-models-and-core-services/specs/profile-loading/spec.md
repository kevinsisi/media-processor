## ADDED Requirements

### Requirement: Profile loader parses YAML into typed model

The system SHALL provide `media_processor.profile.loader.load_profile(path: Path) -> Profile` that reads a YAML file and returns a typed `Profile` object whose attributes mirror spec §5: `name`, `description`, `tag_weights` (dict[str, float]), `filters`, `editing_rules`, `reframe`, `captions`, `face_blur`.

#### Scenario: Load a valid profile

- **WHEN** `load_profile(Path("profiles/carsmeet-luxury.yaml"))` runs
- **THEN** the returned object has `name == "carsmeet-luxury"` and `tag_weights["logo_close_up"] == 1.5`

### Requirement: Loader rejects malformed profiles loudly

The loader SHALL raise `ProfileValidationError` with a human-readable message when required keys are missing, types are wrong, or `editing_rules.target_duration_ms` is not positive.

#### Scenario: Missing top-level field

- **WHEN** a YAML file lacks the `editing_rules` key
- **THEN** `load_profile` raises `ProfileValidationError` mentioning `editing_rules`

#### Scenario: Negative target duration

- **WHEN** a profile sets `editing_rules.target_duration_ms = -1`
- **THEN** `load_profile` raises `ProfileValidationError`

### Requirement: Bundled profiles load without error

The two profiles shipped with the repository (`profiles/carsmeet-luxury.yaml`, `profiles/universal.yaml`) SHALL load successfully under this validator.

#### Scenario: Repo profiles parse

- **WHEN** both repo profiles are loaded
- **THEN** neither raises and both report a non-empty `tag_weights` dict
