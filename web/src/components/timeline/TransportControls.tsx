import { useState } from "react";
import "./TransportControls.css";

// Play/pause + time readout + fine-grained playback speed.
// Speed is a continuous slider (0.25x – 3.0x, step 0.01) plus a
// click-to-edit numeric readout for exact entry, plus three quick-
// jump buttons. HTML5 video supports arbitrary float playbackRate so
// no quantisation is needed.

const SPEED_MIN = 0.25;
const SPEED_MAX = 3.0;
const SPEED_STEP = 0.01;
const QUICK_SPEEDS = [0.5, 1.0, 2.0] as const;

export interface TransportControlsProps {
  isPlaying: boolean;
  onTogglePlay: () => void;
  currentMs: number;
  totalMs: number;
  speed: number;
  onSpeedChange: (v: number) => void;
}

export default function TransportControls({
  isPlaying,
  onTogglePlay,
  currentMs,
  totalMs,
  speed,
  onSpeedChange,
}: TransportControlsProps) {
  const [editingSpeed, setEditingSpeed] = useState(false);
  const [editBuffer, setEditBuffer] = useState("");

  const commitEdit = () => {
    const v = Number.parseFloat(editBuffer);
    if (Number.isFinite(v)) {
      onSpeedChange(clampSpeed(v));
    }
    setEditingSpeed(false);
  };

  return (
    <div className="transport">
      <div className="transport__primary">
        <button
          type="button"
          className="transport__play"
          onClick={onTogglePlay}
          aria-label={isPlaying ? "暫停" : "播放"}
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <span className="transport__time mono">
          {formatMs(currentMs)} / {formatMs(totalMs)}
        </span>
      </div>
      <div className="transport__speed">
        <div className="transport__speed-quick">
          {QUICK_SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              className={`transport__quick ${speed === s ? "transport__quick--active" : ""}`}
              onClick={() => onSpeedChange(s)}
            >
              {s}×
            </button>
          ))}
        </div>
        <input
          type="range"
          className="transport__slider"
          min={SPEED_MIN}
          max={SPEED_MAX}
          step={SPEED_STEP}
          value={speed}
          onChange={(e) => onSpeedChange(Number.parseFloat(e.target.value))}
          aria-label="倍速"
        />
        {editingSpeed ? (
          <input
            type="number"
            className="transport__speed-input"
            min={SPEED_MIN}
            max={SPEED_MAX}
            step={SPEED_STEP}
            value={editBuffer}
            autoFocus
            onChange={(e) => setEditBuffer(e.target.value)}
            onBlur={commitEdit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitEdit();
              if (e.key === "Escape") setEditingSpeed(false);
            }}
          />
        ) : (
          <button
            type="button"
            className="transport__speed-readout mono"
            onClick={() => {
              setEditBuffer(speed.toFixed(2));
              setEditingSpeed(true);
            }}
            title="點擊輸入精確倍速"
          >
            {speed.toFixed(2)}×
          </button>
        )}
      </div>
    </div>
  );
}

function clampSpeed(v: number): number {
  return Math.max(SPEED_MIN, Math.min(SPEED_MAX, v));
}

function formatMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) ms = 0;
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
