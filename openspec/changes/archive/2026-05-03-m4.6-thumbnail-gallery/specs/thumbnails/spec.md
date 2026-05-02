# Asset thumbnails capability

## REQ-1 — Keyframe gallery generation

The system SHALL extract exactly **5 keyframes** from each video Asset at fixed time positions of **10 %, 30 %, 50 %, 70 %, 90 %** of `Asset.duration_ms`, write each as a JPEG to `${THUMBNAILS_DIR}/{asset_id}/frame_{n}.jpg` (n ∈ {0..4}), scaled to **320 px wide** preserving aspect ratio, JPEG quality **80**.

#### Scenario: Generate frames for a fresh asset
- **WHEN** `services.thumbnails.generate(asset_id, path, duration_ms)` is called for an asset whose thumbnail directory is empty
- **THEN** the directory `${THUMBNAILS_DIR}/{asset_id}/` is created
- **AND** five files `frame_0.jpg` … `frame_4.jpg` are written
- **AND** each is decodable as a JPEG with width ≤ 320 px

#### Scenario: Idempotent re-run
- **WHEN** `generate(...)` is called twice in a row without `force=True`
- **THEN** the second call is a no-op for any frame whose file already exists
- **AND** returns `frames_skipped == 5`

#### Scenario: ffmpeg unavailable degrades gracefully
- **WHEN** ffmpeg is missing from the runtime container
- **THEN** `generate(...)` returns a result with `failed_reason="ffmpeg-missing"`
- **AND** does NOT raise

## REQ-2 — Thumbnail URL listing

The API SHALL expose `GET /assets/{id}/thumbnails` returning the asset id, a `count`, and a sorted list of `{index, url}` entries pointing at the static files.

#### Scenario: Asset with 5 frames on disk
- **WHEN** the client GETs `/assets/123/thumbnails` and `/app/media/thumbnails/123/frame_0.jpg` … `frame_4.jpg` exist
- **THEN** the response is `200`, `count: 5`, `thumbnails: [{index: 0, url: "/api/media/thumbnails/123/frame_0.jpg"}, …]`

#### Scenario: Asset with no frames yet
- **WHEN** the client GETs the endpoint for an asset that exists but has no frames on disk
- **THEN** the response is `200`, `count: 0`, `thumbnails: []`
- **AND NOT** a 404 — the gallery placeholder distinguishes "no asset" from "no frames yet" by checking the asset endpoint separately.

#### Scenario: Asset id does not exist
- **WHEN** the client GETs the endpoint for a missing asset
- **THEN** the response is `404`

## REQ-3 — Static file serving

The API SHALL serve generated thumbnail files at the URL prefix `/media/thumbnails/{asset_id}/frame_{n}.jpg`, exposed to the browser as `/api/media/thumbnails/...` through the existing nginx `/api/` proxy. Cache-Control SHALL be `public, max-age=86400, immutable`.

#### Scenario: Browser fetches a thumbnail
- **WHEN** the browser GETs `/api/media/thumbnails/42/frame_2.jpg` after the file was generated on disk
- **THEN** the response is `200` with `Content-Type: image/jpeg`
- **AND** `Cache-Control` includes `max-age=86400`

## REQ-4 — Upload integration

The `POST /uploads/{sid}/complete` endpoint, when kind=video, SHALL trigger thumbnail generation for the newly created Asset before returning. Generation SHALL be best-effort: failures MUST NOT cause the upload to fail.

#### Scenario: Successful upload generates thumbnails
- **WHEN** a chunked video upload completes
- **THEN** five thumbnail files exist under `${THUMBNAILS_DIR}/{new_asset_id}/`
- **AND** the upload-complete response is `200`

#### Scenario: Thumbnail generation fails post-upload
- **WHEN** ffmpeg returns a non-zero exit during generation
- **THEN** the upload-complete response is still `200` with the asset
- **AND** the error is logged (no DB row, no exception bubbles up)
- **AND** the operator can re-generate via the backfill script

## REQ-5 — Project analysis embedding

The system SHALL embed `thumbnail_urls: string[]` (length 0 or 5) for each asset returned by `GET /projects/{id}/assets`, so the polling page renders galleries without an additional round-trip per asset.

#### Scenario: Project with 3 assets, 2 with thumbnails
- **WHEN** the client GETs `/projects/7/assets` and 2 of the 3 assets have all 5 frames
- **THEN** those 2 rows have `thumbnail_urls.length == 5`
- **AND** the third row has `thumbnail_urls == []`

## REQ-6 — Backfill for legacy assets

The repo SHALL ship `scripts/backfill_thumbnails.py` which iterates over every Asset row, skips those whose 5 frames are already present, and runs `generate(...)` for the remainder. The script SHALL be safely re-runnable and SHALL log per-asset success/failure.

#### Scenario: Backfill on a freshly upgraded deployment
- **WHEN** the operator runs `docker compose exec api python -m scripts.backfill_thumbnails`
- **THEN** every Asset without a complete thumbnail set ends up with 5 frames on disk (or a logged failure)
- **AND** the script exits with code `0` on at-least-one-success runs.
