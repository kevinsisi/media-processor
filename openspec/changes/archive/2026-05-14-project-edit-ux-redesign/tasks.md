## 1. RenderOptions

- [x] 1.1 Split toggles into 基本 and AI進階 groups.
- [x] 1.2 Add `hintText` to 防手震 toggle.
- [x] 1.3 Add mutual exclusion: toggling autoReframe clears smartCamera and vice versa at both state and persist-layer handlers.
- [x] 1.4 Add `兩者互斥` label below AI進階 group.

## 2. VersionSwitcher

- [x] 2.1 Add `approved` (green ✓), `failed/rejected` (muted ✗), and latest (`NOW` badge) chip variants.

## 3. StickyGenerateFooter

- [x] 3.1 Create `StickyGenerateFooter.tsx` with `FooterState` union and 6 visual states.
- [x] 3.2 Create `StickyGenerateFooter.css` with sticky positioning and state modifier classes.

## 4. AdvancedEditPanel

- [x] 4.1 Create `AdvancedEditPanel.tsx` with 3-tab bar and ReactNode render-prop content.
- [x] 4.2 Create `AdvancedEditPanel.css`; timeline tab gets full-width body.

## 5. ProjectEdit wiring

- [x] 5.1 Replace `<details>` with `<AdvancedEditPanel>`.
- [x] 5.2 Merge `showFallback` into `showInitial` JSX path.
- [x] 5.3 Scope analysis banner to `showInitial && !analysisComplete`.
- [x] 5.4 Mount `<StickyGenerateFooter>` outside state card; pass correct `footerState`.
- [x] 5.5 Remove `showFailed` inline retry button.

## 6. Completion

- [x] 6.1 Remove dead `.edit-advanced-panel` CSS; fix `--gold` fallback colour.
- [x] 6.2 Run `npm run build` (tsc + vite) — clean.
- [x] 6.3 Commit and push to main.
