import { useEffect, useState } from "react";
import type { AssetDetail, DraftSegmentOut } from "../../api/types";
import "./SegmentInspector.css";

// Properties panel for the currently-selected segment. Edits batch
// locally and commit on blur (or pressing Enter), so a user dragging
// the in/out numeric inputs doesn't fire a PATCH per keystroke.
//
// Includes the [Split at playhead] and [Delete] action buttons since
// both are scoped to "the selected segment".

const TRANSITIONS = [
  "wipeleft",
  "slideright",
  "circlecrop",
  "fade",
  "dissolve",
  "fadeblack",
  "fadewhite",
] as const;

export interface SegmentInspectorProps {
  segment: DraftSegmentOut | null;
  asset: AssetDetail | null;
  /** Current playhead in on-timeline ms — used to enable/disable Split. */
  playheadMs: number;
  busy: boolean;
  error: string | null;
  onPatch: (patch: {
    asset_start_ms?: number;
    asset_end_ms?: number;
    transition?: string;
    voice_volume?: number;
    bgm_volume?: number | null;
  }) => void | Promise<void>;
  onSplit: () => void | Promise<void>;
  onDelete: () => void | Promise<void>;
}

export default function SegmentInspector({
  segment,
  asset,
  playheadMs,
  busy,
  error,
  onPatch,
  onSplit,
  onDelete,
}: SegmentInspectorProps) {
  const [startMs, setStartMs] = useState<string>("");
  const [endMs, setEndMs] = useState<string>("");
  const [voiceVolume, setVoiceVolume] = useState<number>(1.0);
  const [bgmVolume, setBgmVolume] = useState<number | null>(null);

  useEffect(() => {
    if (!segment) return;
    setStartMs(String(segment.asset_start_ms ?? 0));
    setEndMs(String(segment.asset_end_ms ?? 0));
    setVoiceVolume(segment.voice_volume ?? 1.0);
    setBgmVolume(segment.bgm_volume ?? null);
  }, [segment?.id, segment?.asset_start_ms, segment?.asset_end_ms]);

  if (!segment) {
    return (
      <div className="seg-inspector seg-inspector--empty">
        <span>點擊片段以編輯</span>
      </div>
    );
  }

  const splitInsideThisSeg =
    playheadMs > segment.on_timeline_start_ms &&
    playheadMs < segment.on_timeline_end_ms;

  const commitStartMs = () => {
    const v = Number.parseInt(startMs, 10);
    if (Number.isFinite(v) && v !== segment.asset_start_ms) {
      void onPatch({ asset_start_ms: v });
    }
  };
  const commitEndMs = () => {
    const v = Number.parseInt(endMs, 10);
    if (Number.isFinite(v) && v !== segment.asset_end_ms) {
      void onPatch({ asset_end_ms: v });
    }
  };

  return (
    <div className="seg-inspector">
      <div className="seg-inspector__header">
        <h3 className="seg-inspector__title">片段 #{segment.order + 1}</h3>
        <span className="seg-inspector__asset mono">
          {asset?.file_path.split(/[\\/]/).pop() ?? `asset ${segment.asset_id}`}
        </span>
      </div>
      {error && <div className="seg-inspector__error">{error}</div>}
      <div className="seg-inspector__grid">
        <label className="seg-inspector__field">
          <span>素材起點 (ms)</span>
          <input
            type="number"
            value={startMs}
            disabled={busy}
            onChange={(e) => setStartMs(e.target.value)}
            onBlur={commitStartMs}
            onKeyDown={(e) => e.key === "Enter" && commitStartMs()}
          />
        </label>
        <label className="seg-inspector__field">
          <span>素材終點 (ms)</span>
          <input
            type="number"
            value={endMs}
            disabled={busy}
            onChange={(e) => setEndMs(e.target.value)}
            onBlur={commitEndMs}
            onKeyDown={(e) => e.key === "Enter" && commitEndMs()}
          />
        </label>
        <label className="seg-inspector__field">
          <span>轉場</span>
          <select
            value={segment.transition ?? ""}
            disabled={busy}
            onChange={(e) => onPatch({ transition: e.target.value })}
          >
            <option value="">(無預設)</option>
            {TRANSITIONS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="seg-inspector__field">
          <span>人聲音量 ({voiceVolume.toFixed(2)})</span>
          <input
            type="range"
            min={0}
            max={1.5}
            step={0.05}
            value={voiceVolume}
            disabled={busy}
            onChange={(e) => setVoiceVolume(Number.parseFloat(e.target.value))}
            onMouseUp={() => onPatch({ voice_volume: voiceVolume })}
            onTouchEnd={() => onPatch({ voice_volume: voiceVolume })}
          />
        </label>
        <label className="seg-inspector__field">
          <span>
            BGM 音量 ({bgmVolume === null ? "auto" : bgmVolume.toFixed(2)})
          </span>
          <input
            type="range"
            min={0}
            max={1.5}
            step={0.05}
            value={bgmVolume ?? 0}
            disabled={busy || bgmVolume === null}
            onChange={(e) => setBgmVolume(Number.parseFloat(e.target.value))}
            onMouseUp={() =>
              onPatch({ bgm_volume: bgmVolume === null ? null : bgmVolume })
            }
            onTouchEnd={() =>
              onPatch({ bgm_volume: bgmVolume === null ? null : bgmVolume })
            }
          />
          <button
            type="button"
            className="seg-inspector__bgm-toggle"
            onClick={() => {
              const next = bgmVolume === null ? 1.0 : null;
              setBgmVolume(next);
              void onPatch({ bgm_volume: next });
            }}
            disabled={busy}
          >
            {bgmVolume === null ? "覆寫自動" : "改回自動"}
          </button>
        </label>
      </div>
      <div className="seg-inspector__actions">
        <button
          type="button"
          className="cta cta--secondary"
          disabled={busy || !splitInsideThisSeg}
          onClick={() => void onSplit()}
          title={splitInsideThisSeg ? "" : "把播放頭移到此片段內再分割"}
        >
          ✂ 在播放頭分割
        </button>
        <button
          type="button"
          className="cta cta--danger"
          disabled={busy}
          onClick={() => void onDelete()}
        >
          🗑 刪除片段
        </button>
      </div>
    </div>
  );
}
