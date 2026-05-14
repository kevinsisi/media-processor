## Why

Operators expect `預覽原始` and `預覽防抖` to immediately change the visible preview video. On the analysis page, the tracking target thumbnail sits above the variant panel and does not change when preview buttons are pressed, which makes the buttons look broken. Some browsers can also keep the old media element loaded when only the `src` attribute changes.

## What Changes

- Move the asset variant preview panel before the tracking-target picker.
- Show an explicit `正在預覽：原始影片 / 防抖版` label.
- Force the video element to reload when the preview URL changes.
- Mark the selected preview button with `aria-pressed` and selected styling.

## Capabilities

### Modified Capabilities

- `asset-variant-preview`: Raw/stabilized preview controls must visibly switch the preview video source.

## Impact

- Frontend-only ProjectAnalysis hotfix.
- No API or schema change.
