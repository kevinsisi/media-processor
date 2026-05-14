# ProjectEdit UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four structural UX issues in ProjectEdit.tsx — deduplicate EditSettingsBlock, group render-option toggles, replace the `<details>` advanced panel with 3 tabs, add a sticky CTA footer, and scope the analysis banner.

**Architecture:** All changes are frontend-only (no API or backend changes). Two new components are extracted (`StickyGenerateFooter`, `AdvancedEditPanel`). Inline components `RenderOptions` and `VersionSwitcher` are edited in-place inside `ProjectEdit.tsx`. The state machine (showInitial / showQueued / showProcessing / showReady / showFailed / showFallback flags) is preserved unchanged.

**Tech Stack:** React 18, TypeScript 5.6, Vite 5. No test framework — build verification via `cd web && npm run build` (runs `tsc -b && vite build`).

---

## File Map

| Action | File | What changes |
|---|---|---|
| Modify | `web/src/pages/ProjectEdit.tsx` | RenderOptions grouping; VersionSwitcher badge; banner gate; merge showFallback into showInitial branch; remove showFailed inline retry; replace `<details>` with AdvancedEditPanel; add StickyGenerateFooter; add `advancedTab` state + `footerState` computation; import new components |
| Modify | `web/src/pages/ProjectEdit.css` | CSS for toggle groups; version-chip--approved/rejected; padding-bottom for sticky footer |
| Create | `web/src/components/StickyGenerateFooter.tsx` | New component — sticky CTA bar with 5 visual states |
| Create | `web/src/components/StickyGenerateFooter.css` | Styles for sticky footer |
| Create | `web/src/components/AdvancedEditPanel.tsx` | New component — 3-tab panel (設定 / 時間軸 / 字幕) |
| Create | `web/src/components/AdvancedEditPanel.css` | Styles for tab bar and panels |

---

## Task 1 — RenderOptions: toggle grouping, mutual exclusion, hint text

**Files:**
- Modify: `web/src/pages/ProjectEdit.tsx:469–535`
- Modify: `web/src/pages/ProjectEdit.css` (after `.render-options` block, around line 394)

### Step 1.1 — Replace the RenderOptions function body

Find the current `function RenderOptions(...)` (lines 469–535) and replace its entire body:

```tsx
function RenderOptions({
  stabilize,
  setStabilize,
  subtitlesOn,
  setSubtitlesOn,
  transitionsOn,
  setTransitionsOn,
  autoReframe,
  setAutoReframe,
  smartCamera,
  setSmartCamera,
  disabled,
}: RenderOptionsProps) {
  function handleAutoReframe(v: boolean) {
    setAutoReframe(v);
    if (v) setSmartCamera(false);
  }
  function handleSmartCamera(v: boolean) {
    setSmartCamera(v);
    if (v) setAutoReframe(false);
  }

  return (
    <div className="render-options">
      <div className="render-options__group">
        <p className="render-options__group-label">基本</p>
        <EditOptionToggle
          label="畫面防手震"
          hint="手機手持拍攝建議開啟。已使用穩定版的素材不會重複處理。"
          value={stabilize}
          onChange={setStabilize}
          disabled={disabled}
        />
        <EditOptionToggle
          label="加上字幕"
          hint="開啟後會把繁體中文字幕放進影片，適合社群靜音觀看。"
          value={subtitlesOn}
          onChange={setSubtitlesOn}
          disabled={disabled}
        />
        <EditOptionToggle
          label="使用轉場效果"
          hint="開啟後片段之間會更柔順；關閉則節奏更直接。"
          value={transitionsOn}
          onChange={setTransitionsOn}
          disabled={disabled}
        />
      </div>
      <div className="render-options__group">
        <p className="render-options__group-label">
          AI 進階
          <span className="render-options__mutex-hint">（選一種）</span>
        </p>
        <EditOptionToggle
          label="自動跟住主角"
          hint="建立直式或方形影片時，系統會盡量讓人物、車或商品留在畫面中間。"
          value={autoReframe}
          onChange={handleAutoReframe}
          disabled={disabled}
        />
        <EditOptionToggle
          label="AI 智慧運鏡（實驗性）"
          hint="啟用後重新產生時會多打一次 Gemini 規劃鏡頭運動。與跟住主角同時開啟時會自動退讓。"
          value={smartCamera}
          onChange={handleSmartCamera}
          disabled={disabled}
        />
        <p className="render-options__mutex-label">兩者互斥，同時只能啟用一種</p>
      </div>
    </div>
  );
}
```

### Step 1.2 — Add CSS for toggle groups

In `web/src/pages/ProjectEdit.css`, after the existing `.render-options` block (around line 397), add:

```css
.render-options__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-1, 4px);
  padding-bottom: var(--space-3, 12px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  margin-bottom: var(--space-2, 8px);
}

.render-options__group:last-child {
  border-bottom: none;
  padding-bottom: 0;
  margin-bottom: 0;
}

.render-options__group-label {
  font-size: var(--t-xs);
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin-bottom: var(--space-1, 4px);
}

.render-options__mutex-hint {
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  opacity: 0.7;
  margin-left: 4px;
}

.render-options__mutex-label {
  font-size: var(--t-xs);
  color: var(--text-secondary);
  opacity: 0.6;
  margin-top: 2px;
}
```

### Step 1.3 — Build check

```
cd web && npm run build
```

Expected: build succeeds. Verify in browser: toggles appear in two groups; clicking 自動跟住主角 while AI 智慧運鏡 is on should turn off 智慧運鏡 automatically.

### Step 1.4 — Commit

```
git add web/src/pages/ProjectEdit.tsx web/src/pages/ProjectEdit.css
git commit -m "feat(0.43.0): group render toggles, add mutual exclusion + stabilize hint"
```

---

## Task 2 — VersionSwitcher: "NOW" badge + approved/rejected CSS

**Files:**
- Modify: `web/src/pages/ProjectEdit.tsx:244–285`
- Modify: `web/src/pages/ProjectEdit.css` (after existing `.version-chip--failed` block, around line 376)

### Step 2.1 — Edit VersionSwitcher to add isLatest flag

Find the `function VersionSwitcher(...)` (lines 244–285). Replace its body with:

```tsx
function VersionSwitcher({
  drafts,
  selectedId,
  onSelect,
  disabled,
}: VersionSwitcherProps) {
  if (drafts.length === 0) return null;
  const latestId = drafts.reduce((max, d) => (d.id > max ? d.id : max), drafts[0].id);
  return (
    <nav className="version-switcher" aria-label="短影音版本">
      <span className="version-switcher__label">版本</span>
      <div className="version-switcher__chips" role="tablist">
        {drafts.map((d) => {
          const isActive = d.id === selectedId;
          const isLatest = d.id === latestId;
          return (
            <button
              key={d.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={[
                "version-chip",
                `version-chip--${d.status}`,
                isActive ? "version-chip--active" : "",
                isLatest ? "version-chip--latest" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => onSelect(d.id)}
              disabled={disabled}
              title={`v${d.version} · ${labelForDraftStatus(d.status)}`}
            >
              <span className="version-chip__num mono">v{d.version}</span>
              <span className="version-chip__state mono">
                {isLatest ? "NOW" : labelForDraftStatus(d.status)}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
```

### Step 2.2 — Add CSS for approved, rejected, and latest

In `web/src/pages/ProjectEdit.css`, after the `.version-chip--failed .version-chip__state` block (around line 377), add:

```css
.version-chip--approved .version-chip__state {
  color: rgb(98, 184, 122);
}

.version-chip--rejected .version-chip__state {
  color: var(--status-down, #b34b3a);
}

.version-chip--latest .version-chip__state {
  font-weight: 600;
  letter-spacing: 0.08em;
}
```

### Step 2.3 — Build check

```
cd web && npm run build
```

Expected: build succeeds. The latest draft chip shows "NOW" instead of the status label.

### Step 2.4 — Commit

```
git add web/src/pages/ProjectEdit.tsx web/src/pages/ProjectEdit.css
git commit -m "feat(0.43.0): VersionSwitcher NOW badge + approved/rejected chip colors"
```

---

## Task 3 — StickyGenerateFooter component

**Files:**
- Create: `web/src/components/StickyGenerateFooter.tsx`
- Create: `web/src/components/StickyGenerateFooter.css`

### Step 3.1 — Create `StickyGenerateFooter.tsx`

```tsx
import "./StickyGenerateFooter.css";

export type FooterState =
  | "idle"      // showInitial or showFallback, analysis complete
  | "blocked"   // showInitial or showFallback, analysis still running
  | "triggering" // request in-flight or showProcessing
  | "queued"    // showQueued
  | "failed"    // showFailed
  | "ready";    // showReady — re-generate action

interface StickyGenerateFooterProps {
  state: FooterState;
  label: string;
  disabled?: boolean;
  onClick: () => void;
  onOpenQueue: () => void;
}

export default function StickyGenerateFooter({
  state,
  label,
  disabled,
  onClick,
  onOpenQueue,
}: StickyGenerateFooterProps) {
  const isQueued = state === "queued";
  const isProcessing = state === "triggering";
  const isFailed = state === "failed";
  const isBlocked = state === "blocked";
  const isDisabled = disabled || isProcessing || isBlocked;

  return (
    <div className="sticky-footer">
      <div className="sticky-footer__inner">
        {isQueued ? (
          <button
            type="button"
            className="sticky-footer__btn sticky-footer__btn--queued"
            onClick={onOpenQueue}
          >
            {label}
          </button>
        ) : (
          <button
            type="button"
            className={[
              "sticky-footer__btn",
              isFailed ? "sticky-footer__btn--failed" : "",
              isProcessing ? "sticky-footer__btn--processing" : "",
              isBlocked ? "sticky-footer__btn--blocked" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            onClick={onClick}
            disabled={isDisabled}
          >
            {isProcessing && (
              <span className="sticky-footer__spinner" aria-hidden />
            )}
            {label}
          </button>
        )}
      </div>
    </div>
  );
}
```

### Step 3.2 — Create `StickyGenerateFooter.css`

```css
.sticky-footer {
  position: sticky;
  bottom: 0;
  z-index: 10;
  background: linear-gradient(to top, var(--surface-bg, #0a0f1e) 70%, transparent);
  padding: 16px 24px 20px;
  pointer-events: none;
}

.sticky-footer__inner {
  pointer-events: all;
  max-width: 640px;
  margin: 0 auto;
}

.sticky-footer__btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px 24px;
  border-radius: 10px;
  font-size: var(--t-md, 15px);
  font-weight: 600;
  border: none;
  cursor: pointer;
  transition: opacity 0.15s, background 0.15s;
  background: var(--gold, #3b82f6);
  color: #fff;
}

.sticky-footer__btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.sticky-footer__btn--failed {
  background: transparent;
  border: 2px solid rgba(179, 75, 58, 0.8);
  color: #e07060;
}

.sticky-footer__btn--failed:hover:not(:disabled) {
  background: rgba(179, 75, 58, 0.15);
}

.sticky-footer__btn--queued {
  background: rgba(255, 193, 7, 0.15);
  border: 1px solid rgba(255, 193, 7, 0.5);
  color: #fcd34d;
}

.sticky-footer__btn--processing {
  background: rgba(59, 130, 246, 0.3);
  color: #93c5fd;
}

.sticky-footer__btn--blocked {
  background: var(--surface-mid, #1e293b);
  color: var(--text-secondary);
}

.sticky-footer__spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
```

### Step 3.3 — Build check

```
cd web && npm run build
```

Expected: new files compile without errors.

### Step 3.4 — Commit

```
git add web/src/components/StickyGenerateFooter.tsx web/src/components/StickyGenerateFooter.css
git commit -m "feat(0.43.0): add StickyGenerateFooter component"
```

---

## Task 4 — AdvancedEditPanel component

**Files:**
- Create: `web/src/components/AdvancedEditPanel.tsx`
- Create: `web/src/components/AdvancedEditPanel.css`

### Step 4.1 — Create `AdvancedEditPanel.tsx`

```tsx
import { type ReactNode } from "react";
import "./AdvancedEditPanel.css";

export type AdvancedTab = "settings" | "timeline" | "subtitles";

interface AdvancedEditPanelProps {
  activeTab: AdvancedTab;
  onTabChange: (tab: AdvancedTab) => void;
  settingsContent: ReactNode;
  timelineContent: ReactNode;
  subtitlesContent: ReactNode;
}

const TABS: { id: AdvancedTab; label: string }[] = [
  { id: "settings", label: "⚙ 設定" },
  { id: "timeline", label: "🎞 時間軸" },
  { id: "subtitles", label: "💬 字幕" },
];

export default function AdvancedEditPanel({
  activeTab,
  onTabChange,
  settingsContent,
  timelineContent,
  subtitlesContent,
}: AdvancedEditPanelProps) {
  return (
    <div className="adv-panel">
      <div className="adv-panel__tab-bar" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={activeTab === t.id}
            className={[
              "adv-panel__tab",
              activeTab === t.id ? "adv-panel__tab--active" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            onClick={() => onTabChange(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        className={[
          "adv-panel__body",
          activeTab === "timeline" ? "adv-panel__body--fullwidth" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        role="tabpanel"
      >
        {activeTab === "settings" && settingsContent}
        {activeTab === "timeline" && timelineContent}
        {activeTab === "subtitles" && subtitlesContent}
      </div>
    </div>
  );
}
```

### Step 4.2 — Create `AdvancedEditPanel.css`

```css
.adv-panel {
  margin-top: var(--space-5, 20px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.025);
  overflow: hidden;
}

.adv-panel__tab-bar {
  display: flex;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
}

.adv-panel__tab {
  flex: 1;
  padding: 12px 16px;
  font-size: var(--t-sm, 13px);
  font-weight: 500;
  color: var(--text-secondary);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}

.adv-panel__tab:hover {
  color: var(--text-primary);
}

.adv-panel__tab--active {
  color: var(--gold, #ffd882);
  border-bottom-color: var(--gold, #ffd882);
}

.adv-panel__body {
  padding: var(--space-5, 20px);
}

/* Timeline tab: remove horizontal padding so DraggableTimeline uses full width */
.adv-panel__body--fullwidth {
  padding-left: 0;
  padding-right: 0;
}
```

### Step 4.3 — Build check

```
cd web && npm run build
```

Expected: new files compile without errors.

### Step 4.4 — Commit

```
git add web/src/components/AdvancedEditPanel.tsx web/src/components/AdvancedEditPanel.css
git commit -m "feat(0.43.0): add AdvancedEditPanel 3-tab component"
```

---

## Task 5 — ProjectEdit.tsx wiring

**Files:**
- Modify: `web/src/pages/ProjectEdit.tsx` (multiple targeted edits)
- Modify: `web/src/pages/ProjectEdit.css` (add padding-bottom)

This task has 6 sub-steps. Complete them in order; build after each.

### Step 5.1 — Add imports and `advancedTab` state

**5.1a** — Add imports after the existing import block (around line 30). Add these two lines alongside the other component imports:

```tsx
import StickyGenerateFooter, { type FooterState } from "../components/StickyGenerateFooter";
import AdvancedEditPanel, { type AdvancedTab } from "../components/AdvancedEditPanel";
```

**5.1b** — Add `advancedTab` state. Find the block of `useState` declarations inside `function ProjectEdit()` (typically around line 400–450 where states like `triggering`, `stabilize`, etc. are declared). Add after the last existing `useState`:

```tsx
const [advancedTab, setAdvancedTab] = useState<AdvancedTab>("settings");
```

**5.1c** — Add `footerState` + `footerLabel` + `footerOnClick` computed values. Add these three constants right before the `return (` statement of the component:

```tsx
const footerState: FooterState = (() => {
  if (showQueued) return "queued";
  if (triggering || showProcessing) return "triggering";
  if (showFailed) return "failed";
  if (showReady) return "ready";
  if (analysisBlocked) return "blocked";
  return "idle";
})();

const footerLabel = (() => {
  switch (footerState) {
    case "queued":     return "排隊中，點此查看";
    case "triggering": return "產生中…";
    case "failed":     return "↺ 重試";
    case "ready":      return `↺ 重新產生（${durationSec} 秒）`;
    case "blocked":    return `素材檢查中（剩 ${analysisStatus?.inFlight ?? 0} 項），請稍候`;
    case "idle":       return `▶ 產生 ${durationSec} 秒短影音`;
  }
})();

const footerOnClick = (() => {
  switch (footerState) {
    case "queued":     return () => setShowQueueModal(true);
    case "failed":     return () => { void handleStartEdit(true); };
    case "ready":      return () => { void handleStartEdit(true); };
    case "idle":       return showFallback
      ? () => { void handleStartEdit(true); }
      : () => { void handleStartEdit(false); };
    default:           return () => {};
  }
})();
```

*(Note: `setShowQueueModal` is the existing state setter for the queue modal — search for it in the file to confirm the exact name.)*

**Build check after 5.1:**
```
cd web && npm run build
```
Expected: compiles. `advancedTab` is declared but not yet used — TypeScript may warn if `noUnusedLocals` is set; it will be used in 5.4.

### Step 5.2 — Scope the analysis banner to showInitial

Find line 1578 (the analysis banner condition):

```tsx
{analysisStatus !== null && !analysisStatus.allDone ? (
```

Change it to:

```tsx
{showInitial && analysisStatus !== null && !analysisStatus.allDone ? (
```

**Build check:**
```
cd web && npm run build
```

### Step 5.3 — Merge showFallback into the showInitial section

**Goal:** eliminate the third `EditSettingsBlock` in the showFallback branch.

Find the `showInitial` section opening tag (line ~1618):

```tsx
{showInitial && (
  <section className="edit-card">
    <h2 className="edit-card__title">準備好就產生短影音</h2>
    <p className="edit-card__body">
      系統會依照腳本與影片內容…
    </p>
```

Change `{showInitial && (` to `{(showInitial || showFallback) && (` and replace the static title + body with a conditional:

```tsx
{(showInitial || showFallback) && (
  <section className="edit-card">
    {showFallback ? (
      <>
        <h2 className="edit-card__title">再產生一版短影音</h2>
        <p className="edit-card__body">
          目前選取的版本（v{selectedSummary?.version ?? "?"}，
          {selectedSummary
            ? labelForDraftStatus(selectedSummary.status)
            : "未知狀態"}
          ）沒有額外動作可以執行。如需建立新的版本，可調整下方設定後重新產生。
        </p>
      </>
    ) : (
      <>
        <h2 className="edit-card__title">準備好就產生短影音</h2>
        <p className="edit-card__body">
          系統會依照腳本與影片內容，挑出適合社群觀看的片段，做成可發佈的
          IG / FB 短影音，並加上繁體中文字幕。
        </p>
      </>
    )}
    <EditSettingsBlock
      durationSec={durationSec}
      setDurationSec={handleDurationChange}
      stylePreset={stylePreset}
      setStylePreset={handleStylePresetChange}
      stabilize={stabilize}
      setStabilize={handleStabilizeChange}
      subtitlesOn={subtitlesOn}
      setSubtitlesOn={handleSubtitlesChange}
      transitionsOn={transitionsOn}
      setTransitionsOn={handleTransitionsChange}
      autoReframe={autoReframe}
      setAutoReframe={handleAutoReframeChange}
      smartCamera={smartCamera}
      setSmartCamera={handleSmartCameraChange}
      sourceAudioVolume={sourceAudioVolume}
      setSourceAudioVolume={handleSourceAudioVolumeChange}
      triggering={triggering}
      validProjectId={validProjectId}
      project={project}
      setProject={handleProjectSettingsUpdated}
      currentBgmSource={currentBgmSource}
      setCurrentBgmSource={setCurrentBgmSource}
      pendingSettingsNotice={pendingSettingsNotice}
      settingsApplyHint={`按下方「產生 ${durationSec} 秒短影音」才會依照目前設定建立${showFallback ? "新版" : ""}成品。`}
      cropDirection={cropDirection}
    />
    {/* CTA button removed — moved to StickyGenerateFooter */}
  </section>
)}
```

Then **delete the entire showFallback section** (lines ~2003–2060). The section starts with `{showFallback && (` and ends with `)}` after the actions div.

**Build check:**
```
cd web && npm run build
```

### Step 5.4 — Replace `<details>` with AdvancedEditPanel

Find (line ~1814):

```tsx
<details className="edit-advanced-panel">
  <summary className="edit-advanced-panel__summary">
    <span className="edit-advanced-panel__title">進階微調</span>
    <span className="edit-advanced-panel__hint">
      需要改片段、字幕、配樂或品牌標誌時再打開。
    </span>
  </summary>
  ...
</details>
```

Replace the entire `<details>` block (lines 1814–1953) with:

```tsx
<AdvancedEditPanel
  activeTab={advancedTab}
  onTabChange={setAdvancedTab}
  settingsContent={
    <section className="edit-card edit-card--secondary">
      <div className="edit-card__row">
        <div>
          <h2 className="edit-card__title">片段與設定微調</h2>
          <p className="edit-card__body">
            這裡保留給需要手動調整的人；一般發佈可直接使用上方工作台。
          </p>
        </div>
        <div className="edit-card__actions">
          {draft.mp4_url && (
            <a
              className="cta cta--quiet"
              href={draft.mp4_url}
              download={`project-${validProjectId}-v${draft.version}.mp4`}
            >
              下載主成品
            </a>
          )}
          <button
            type="button"
            className="cta cta--secondary"
            onClick={() => void handleReRender()}
            disabled={triggering}
            title="保留目前片段順序，套用目前的焦點追蹤、裁切、配樂、字幕、品牌標誌與轉場設定重新產生成品"
          >
            {triggering ? "送出中…" : "保留片段套用設定"}
          </button>
          <button
            type="button"
            className="cta"
            onClick={() => void handleStartEdit(true)}
            disabled={triggering || analysisBlocked}
            title={
              analysisBlocked
                ? "等待素材檢查完成後即可重新挑選片段"
                : "重新挑選片段；目前的順序會被覆蓋"
            }
          >
            {triggering
              ? "送出中…"
              : analysisBlocked
                ? `素材檢查中（剩 ${analysisStatus?.inFlight ?? 0} 項）`
                : `重新選片段（${durationSec} 秒）`}
          </button>
        </div>
      </div>
      <EditSettingsBlock
        durationSec={durationSec}
        setDurationSec={handleDurationChange}
        stylePreset={stylePreset}
        setStylePreset={handleStylePresetChange}
        stabilize={stabilize}
        setStabilize={handleStabilizeChange}
        subtitlesOn={subtitlesOn}
        setSubtitlesOn={handleSubtitlesChange}
        transitionsOn={transitionsOn}
        setTransitionsOn={handleTransitionsChange}
        autoReframe={autoReframe}
        setAutoReframe={handleAutoReframeChange}
        smartCamera={smartCamera}
        setSmartCamera={handleSmartCameraChange}
        sourceAudioVolume={sourceAudioVolume}
        setSourceAudioVolume={handleSourceAudioVolumeChange}
        triggering={triggering}
        validProjectId={validProjectId}
        project={project}
        setProject={handleProjectSettingsUpdated}
        currentBgmSource={currentBgmSource}
        setCurrentBgmSource={setCurrentBgmSource}
        pendingSettingsNotice={pendingSettingsNotice}
        settingsApplyHint="只想套用目前配樂、字幕、品牌、裁切或跟住主角設定，請按「保留片段套用設定」；如果想重新挑片段，按「重新選片段」。"
        cropDirection={cropDirection}
      />
      <div className="edit-card__advanced-row">
        <Link
          to={`/projects/${validProjectId}/edit/timeline/${draft.id}`}
          className="cta cta--secondary edit-card__advanced-link"
        >
          進階片段編輯
        </Link>
        <span className="edit-card__advanced-hint">
          打開時間軸，可調整順序、分割或刪除片段
        </span>
      </div>
      {draft.cut_plan?.notes && (
        <p className="edit-card__hint mono">「{draft.cut_plan.notes}」</p>
      )}
      {draft.cut_plan?.used_fallback && (
        <p className="edit-hint">
          已用保守方式產生成品（{draft.cut_plan.fallback_reason || "原因未明"}）。
        </p>
      )}
    </section>
  }
  timelineContent={
    <DraggableTimeline
      draft={draft}
      videoRef={videoRef as React.RefObject<HTMLVideoElement>}
      assetThumbs={assetThumbs}
      onReorderStart={() => void refreshDrafts().catch(() => {})}
      onReorderCommitted={(fresh) => {
        polling.applyDraft(fresh);
        setPendingSettingsNotice(null);
        void refreshDrafts().catch(() => {});
      }}
      onReorderError={(msg) => setTriggerError(msg)}
      renderFlags={{
        transitions: transitionsOn,
        stabilize,
        subtitles: subtitlesOn,
        autoReframe,
        smartCamera,
      }}
    />
  }
  subtitlesContent={
    <SubtitleEditor
      draftId={draft.id}
      locked={triggering || awaitingFirstFetch || showProcessing}
      onRebuildStart={() => void refreshDrafts().catch(() => {})}
      onRebuildError={(msg) => setTriggerError(msg)}
      renderFlags={{
        transitions: transitionsOn,
        stabilize,
        subtitles: subtitlesOn,
        autoReframe,
        smartCamera,
      }}
    />
  }
/>
```

**Build check:**
```
cd web && npm run build
```

### Step 5.5 — Remove showFailed inline retry button; add StickyGenerateFooter

**5.5a** — In the `showFailed` section (lines ~1957–2001), remove the `<div className="edit-card__actions">` block that contains the retry `<button>`. Keep the title, body text, `<ProgressTracker>`, and `<details className="edit-card__error-details">`. The section should end at the error details.

The remaining showFailed card looks like:

```tsx
{showFailed && draft && (() => {
  const isOrphan = (draft.prompt_feedback || "").startsWith("render: orphaned");
  return (
    <section className="edit-card edit-card--failed">
      <h2 className="edit-card__title">
        {isOrphan ? "這次沒有完成" : "短影音產生失敗"}
      </h2>
      <p className="edit-card__body">
        {isOrphan
          ? "這次產生成品的處理中斷或逾時，沒有成功完成。請點下方按鈕重新送出。"
          : "這次成品沒有成功產出。下方會標出停在哪一步；常見原因是素材不夠、AI 暫時忙碌，或某段影片格式不穩。"}
      </p>
      {!isOrphan && <ProgressTracker steps={draft.progress_steps} />}
      {draft.prompt_feedback && (
        <details className="edit-card__error-details">
          <summary>展開錯誤細節（給開發者參考）</summary>
          <pre className="edit-card__error mono">{draft.prompt_feedback}</pre>
        </details>
      )}
    </section>
  );
})()}
```

**5.5b** — Add `<StickyGenerateFooter>` just before the `<DraftComments>` line (line ~2062). Find:

```tsx
{selectedDraftId !== null && <DraftComments draftId={selectedDraftId} />}
```

Insert before it:

```tsx
<StickyGenerateFooter
  state={footerState}
  label={footerLabel}
  onClick={footerOnClick}
  onOpenQueue={() => setShowQueueModal(true)}
/>
```

**Build check:**
```
cd web && npm run build
```

### Step 5.6 — Add padding-bottom to prevent sticky footer overlapping content

In `web/src/pages/ProjectEdit.css`, find the main page container selector (likely `.project-edit` or `.edit-page` — search for the topmost wrapper class). Add:

```css
.project-edit {          /* replace with actual class name */
  padding-bottom: 100px;
}
```

*(Search `ProjectEdit.tsx` for `className="project-edit"` or similar to find the exact class name.)*

**Final build check:**
```
cd web && npm run build
```

Expected: clean build. Zero TypeScript errors.

### Step 5.7 — Commit

```
git add web/src/pages/ProjectEdit.tsx web/src/pages/ProjectEdit.css
git commit -m "feat(0.43.0): wire StickyGenerateFooter + AdvancedEditPanel, merge showFallback, scope analysis banner"
```

---

## Task 6 — Integration push

### Step 6.1 — Final build + manual smoke check

```
cd web && npm run build
```

Check in browser (or via `npm run dev`):
- [ ] showInitial: two-group toggles; analysis banner gone when analysis is complete; generate button shows in sticky footer
- [ ] showFallback: shows "再產生一版" text with settings, no duplicate EditSettingsBlock
- [ ] showReady: AdvancedEditPanel has three tabs; 時間軸 tab has full-width DraggableTimeline
- [ ] showFailed: no inline retry button; sticky footer shows red "↺ 重試"
- [ ] Mutual exclusion: toggling 自動跟住主角 ON disables AI 智慧運鏡

### Step 6.2 — Push

```
git push
```

---

## Self-Review Notes

- `setShowQueueModal` name: confirm the exact setter name for the queue modal state in `ProjectEdit.tsx` before Task 5.1c. Search `showQueueModal` in the file.
- `handleReRender` name: confirm the exact function name for the "保留片段套用設定" action in `ProjectEdit.tsx`. Search `handleReRender` or `reRender`.
- The main page container class for Task 5.6: search `className="` near the root `return (` of `ProjectEdit` to find the topmost div's class name.
- `footerOnClick` for `"triggering"` and `"blocked"` states returns `() => {}` (no-op) since the button is visually disabled; verify the `isDisabled` logic in `StickyGenerateFooter.tsx` covers these cases correctly.
