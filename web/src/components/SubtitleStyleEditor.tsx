// v0.18 — project-level subtitle style picker. Renders inside ProjectEdit
// next to the BGM / render-options block; PATCHes /projects/{id}/subtitle-
// style on each change and surfaces a live drawtext-equivalent preview so
// the user can see font / colour / outline / position without re-rendering.

import { useCallback, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  ProjectDetail,
  SubtitleFont,
  SubtitleOutlineWidth,
  SubtitlePosition,
  SubtitleSize,
  SubtitleStylePatch,
} from "../api/types";
import "./SubtitleStyleEditor.css";

interface SubtitleStyleEditorProps {
  project: ProjectDetail | null;
  // Called after a successful PATCH so the parent can refresh its local
  // state. Sends the same ProjectDetail the API returned.
  onProjectUpdated: (next: ProjectDetail) => void;
  disabled?: boolean;
}

const FONT_LABELS: { value: SubtitleFont; label: string; family: string }[] = [
  // ``family`` is the CSS font-family used in the live preview only.
  // The actual render uses the matching ttc inside the worker container —
  // these CSS families are best-effort browser-side approximations.
  { value: "noto_sans_tc", label: "Noto Sans CJK TC（預設）", family: '"Noto Sans TC", "Noto Sans CJK TC", system-ui, sans-serif' },
  { value: "noto_sans_tc_bold", label: "Noto Sans CJK TC 粗體", family: '"Noto Sans TC", "Noto Sans CJK TC", system-ui, sans-serif' },
  { value: "noto_serif_tc", label: "Noto Serif CJK TC", family: '"Noto Serif TC", "Noto Serif CJK TC", "Source Han Serif", serif' },
];

const POSITION_OPTIONS: { value: SubtitlePosition; label: string }[] = [
  { value: "top", label: "上方" },
  { value: "middle", label: "中央" },
  { value: "bottom", label: "下方" },
];

const SIZE_OPTIONS: { value: SubtitleSize; label: string; px: number }[] = [
  // Pixel values mirror video_renderer.SUBTITLE_SIZE_CHOICES so the
  // preview is roughly to-scale against an actual render canvas.
  { value: "small", label: "小", px: 32 },
  { value: "medium", label: "中", px: 42 },
  { value: "large", label: "大", px: 56 },
];

const OUTLINE_OPTIONS: { value: SubtitleOutlineWidth; label: string; px: number }[] = [
  { value: "none", label: "無描邊", px: 0 },
  { value: "thin", label: "細描邊", px: 2 },
  { value: "thick", label: "粗描邊", px: 5 },
];

const PREVIEW_TEXT = "繁體中文預覽 ABC 123";
// Preview area is a fixed 16:9 sliver scaled down from the full render
// canvas. The drawtext font sizes are pixel-accurate for a 1080-tall
// frame; the preview is 200 px tall so we scale by 200/1080 ≈ 0.185 and
// floor to keep the preview within its box without overflowing.
const PREVIEW_HEIGHT_PX = 200;
const RENDER_CANVAS_HEIGHT_PX = 1920;
const PREVIEW_SCALE = PREVIEW_HEIGHT_PX / RENDER_CANVAS_HEIGHT_PX;

function previewFontPx(size: SubtitleSize): number {
  const opt = SIZE_OPTIONS.find((o) => o.value === size);
  return Math.max(11, Math.round((opt?.px ?? 42) * PREVIEW_SCALE * 4));
}

function previewOutlinePx(width: SubtitleOutlineWidth): number {
  const opt = OUTLINE_OPTIONS.find((o) => o.value === width);
  if (!opt || opt.px === 0) return 0;
  return Math.max(1, Math.round(opt.px * PREVIEW_SCALE * 4));
}

function previewFontFamily(font: SubtitleFont): string {
  return FONT_LABELS.find((o) => o.value === font)?.family ?? "system-ui";
}

function previewFontWeight(font: SubtitleFont): number {
  return font === "noto_sans_tc_bold" ? 700 : 400;
}

// Build a CSS text-shadow that approximates the drawtext borderw=N
// outline by stacking offsets in 8 cardinal directions. Browsers don't
// expose a stroke-text API that's broadly supported.
function previewTextShadow(width: number, color: string): string {
  if (width <= 0) return "none";
  const offsets: [number, number][] = [];
  for (let dx = -width; dx <= width; dx++) {
    for (let dy = -width; dy <= width; dy++) {
      if (dx === 0 && dy === 0) continue;
      offsets.push([dx, dy]);
    }
  }
  return offsets.map(([dx, dy]) => `${dx}px ${dy}px 0 ${color}`).join(", ");
}

function justifyForPosition(position: SubtitlePosition): string {
  if (position === "top") return "flex-start";
  if (position === "middle") return "center";
  return "flex-end";
}

export default function SubtitleStyleEditor({
  project,
  onProjectUpdated,
  disabled,
}: SubtitleStyleEditorProps) {
  const [error, setError] = useState<string | null>(null);
  const [savingField, setSavingField] = useState<string | null>(null);
  // Coalesce rapid colour-picker drags into one PATCH per stable colour
  // — chrome fires "input" continuously while the user moves the cursor,
  // so we debounce by 300ms before hitting the server.
  const colorDebounce = useRef<{ [k: string]: number | null }>({});

  const send = useCallback(
    async (field: keyof SubtitleStylePatch, payload: SubtitleStylePatch) => {
      if (!project) return;
      setSavingField(field);
      setError(null);
      try {
        const next = await apiClient.patchProjectSubtitleStyle(
          project.id,
          payload,
        );
        onProjectUpdated(next);
      } catch (err) {
        if (err instanceof ApiError) {
          setError(`儲存失敗（${err.status}）：${err.message}`);
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setSavingField(null);
      }
    },
    [project, onProjectUpdated],
  );

  const debouncedColor = useCallback(
    (field: "subtitle_color" | "subtitle_outline_color", value: string) => {
      const handle = colorDebounce.current[field];
      if (handle) window.clearTimeout(handle);
      colorDebounce.current[field] = window.setTimeout(() => {
        void send(field, { [field]: value } as SubtitleStylePatch);
      }, 300);
    },
    [send],
  );

  // Local mirror of the colour pickers so the preview updates on every
  // pointer move without waiting for the PATCH to round-trip.
  const [localColor, setLocalColor] = useState<string | null>(null);
  const [localOutlineColor, setLocalOutlineColor] = useState<string | null>(
    null,
  );

  const effectiveColor = localColor ?? project?.subtitle_color ?? "#ffffff";
  const effectiveOutlineColor =
    localOutlineColor ?? project?.subtitle_outline_color ?? "#000000";

  const previewStyle = useMemo(() => {
    if (!project) return {} as React.CSSProperties;
    const fontSize = previewFontPx(project.subtitle_size);
    const outlinePx = previewOutlinePx(project.subtitle_outline_width);
    return {
      color: effectiveColor,
      fontFamily: previewFontFamily(project.subtitle_font),
      fontWeight: previewFontWeight(project.subtitle_font),
      fontSize: `${fontSize}px`,
      lineHeight: 1.15,
      textShadow: previewTextShadow(outlinePx, effectiveOutlineColor),
      whiteSpace: "nowrap" as const,
    };
  }, [project, effectiveColor, effectiveOutlineColor]);

  if (!project) {
    return (
      <section className="subtitle-style" aria-label="字幕樣式">
        <header className="subtitle-style__head">
          <h3 className="subtitle-style__title">字幕樣式</h3>
        </header>
        <p className="subtitle-style__hint mono">載入專案中…</p>
      </section>
    );
  }

  const lockField = (field: string) => disabled || savingField === field;

  return (
    <section className="subtitle-style" aria-label="字幕樣式">
      <header className="subtitle-style__head">
        <h3 className="subtitle-style__title">字幕樣式</h3>
        {savingField && (
          <span className="subtitle-style__saving mono" aria-live="polite">
            儲存中…
          </span>
        )}
      </header>

      <div
        className="subtitle-style__preview"
        style={{
          height: PREVIEW_HEIGHT_PX,
          justifyContent: justifyForPosition(project.subtitle_position),
        }}
        aria-label="字幕樣式預覽"
      >
        <div className="subtitle-style__preview-text" style={previewStyle}>
          {PREVIEW_TEXT}
        </div>
      </div>

      <div className="subtitle-style__grid">
        <label className="subtitle-style__field">
          <span className="subtitle-style__label">字體</span>
          <select
            value={project.subtitle_font}
            disabled={lockField("subtitle_font")}
            onChange={(e) =>
              void send("subtitle_font", {
                subtitle_font: e.currentTarget.value as SubtitleFont,
              })
            }
          >
            {FONT_LABELS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        <label className="subtitle-style__field">
          <span className="subtitle-style__label">大小</span>
          <select
            value={project.subtitle_size}
            disabled={lockField("subtitle_size")}
            onChange={(e) =>
              void send("subtitle_size", {
                subtitle_size: e.currentTarget.value as SubtitleSize,
              })
            }
          >
            {SIZE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}（{opt.px}px）
              </option>
            ))}
          </select>
        </label>

        <label className="subtitle-style__field">
          <span className="subtitle-style__label">位置</span>
          <select
            value={project.subtitle_position}
            disabled={lockField("subtitle_position")}
            onChange={(e) =>
              void send("subtitle_position", {
                subtitle_position: e.currentTarget.value as SubtitlePosition,
              })
            }
          >
            {POSITION_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        <label className="subtitle-style__field">
          <span className="subtitle-style__label">描邊粗細</span>
          <select
            value={project.subtitle_outline_width}
            disabled={lockField("subtitle_outline_width")}
            onChange={(e) =>
              void send("subtitle_outline_width", {
                subtitle_outline_width: e.currentTarget
                  .value as SubtitleOutlineWidth,
              })
            }
          >
            {OUTLINE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        <label className="subtitle-style__field subtitle-style__field--color">
          <span className="subtitle-style__label">文字顏色</span>
          <span className="subtitle-style__color-row">
            <input
              type="color"
              value={effectiveColor}
              disabled={lockField("subtitle_color")}
              onChange={(e) => {
                const v = e.currentTarget.value;
                setLocalColor(v);
                debouncedColor("subtitle_color", v);
              }}
            />
            <span className="mono">{effectiveColor}</span>
          </span>
        </label>

        <label className="subtitle-style__field subtitle-style__field--color">
          <span className="subtitle-style__label">描邊顏色</span>
          <span className="subtitle-style__color-row">
            <input
              type="color"
              value={effectiveOutlineColor}
              disabled={
                lockField("subtitle_outline_color") ||
                project.subtitle_outline_width === "none"
              }
              onChange={(e) => {
                const v = e.currentTarget.value;
                setLocalOutlineColor(v);
                debouncedColor("subtitle_outline_color", v);
              }}
            />
            <span className="mono">{effectiveOutlineColor}</span>
          </span>
        </label>
      </div>

      {error && (
        <p className="subtitle-style__error" role="alert">
          {error}
        </p>
      )}
      <p className="subtitle-style__hint mono">
        改動會立即儲存，下次「重新產生」時套用。預覽為近似效果，實際成品可能略有差異。
      </p>
    </section>
  );
}
