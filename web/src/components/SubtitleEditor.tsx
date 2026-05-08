import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { SubtitleCueOut } from "../api/types";
import "./SubtitleEditor.css";

interface SubtitleEditorProps {
  draftId: number;
  // When the draft is mid-render, hide the editor — cues will be rebuilt.
  locked: boolean;
  onRebuildStart?: () => void;
  onRebuildError?: (msg: string) => void;
  // v0.21.3 — current ProjectEdit toggle state. Sent as render_flags
  // override on rebuild-subtitles so a legacy draft (NULL snapshot)
  // re-renders honouring the operator's current toggles instead of
  // silently defaulting to all-True. Optional — when omitted the
  // backend keeps using its stored snapshot.
  renderFlags?: {
    transitions: boolean;
    stabilize: boolean;
    subtitles: boolean;
    autoReframe: boolean;
    smartCamera: boolean;
  };
}

function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  const remMs = Math.max(0, ms - total * 1000);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(Math.floor(remMs / 100)).padStart(1, "0")}`;
}

/**
 * M7.2 — list cues, tap to edit, blur to save (debounced 500 ms). After
 * the user touches anything we surface a "重新燒入字幕" CTA that triggers
 * `rebuild-subtitles`; the worker re-burns the SRT from DB rows so the
 * edits show up in the rendered mp4.
 */
export default function SubtitleEditor({
  draftId,
  locked,
  onRebuildStart,
  onRebuildError,
  renderFlags,
}: SubtitleEditorProps) {
  const [cues, setCues] = useState<SubtitleCueOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [edited, setEdited] = useState<boolean>(false);
  const [rebuilding, setRebuilding] = useState(false);
  // Pending text per idx — flushed to API after debounce.
  const pendingRef = useRef<Map<number, string>>(new Map());
  const debounceRef = useRef<Map<number, number>>(new Map());

  const fetchOnce = useCallback(async () => {
    try {
      const list = await apiClient.fetchDraftSubtitles(draftId);
      setCues(list);
      setLoadError(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // 404 just means the draft has no cues yet — keep the editor mute
      // rather than scary-error.
      if (err instanceof ApiError && err.status === 404) {
        setCues([]);
      } else {
        setLoadError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    setLoading(true);
    setEdited(false);
    void fetchOnce();
  }, [fetchOnce]);

  const flushOne = useCallback(
    async (idx: number) => {
      const text = pendingRef.current.get(idx);
      if (text === undefined) return;
      pendingRef.current.delete(idx);
      try {
        await apiClient.patchDraftSubtitle(draftId, idx, { text });
        setEdited(true);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        onRebuildError?.(`字幕儲存失敗 (#${idx})：${msg}`);
      }
    },
    [draftId, onRebuildError],
  );

  const handleChange = useCallback(
    (idx: number, value: string) => {
      pendingRef.current.set(idx, value);
      // Optimistic local update so the textarea doesn't bounce.
      setCues((prev) =>
        prev.map((c) => (c.idx === idx ? { ...c, text: value } : c)),
      );
      const existing = debounceRef.current.get(idx);
      if (existing) window.clearTimeout(existing);
      const handle = window.setTimeout(() => {
        debounceRef.current.delete(idx);
        void flushOne(idx);
      }, 500);
      debounceRef.current.set(idx, handle);
    },
    [flushOne],
  );

  const handleBlur = useCallback(
    (idx: number) => {
      const handle = debounceRef.current.get(idx);
      if (handle) {
        window.clearTimeout(handle);
        debounceRef.current.delete(idx);
      }
      void flushOne(idx);
    },
    [flushOne],
  );

  const triggerRebuild = useCallback(async () => {
    onRebuildStart?.();
    setRebuilding(true);
    try {
      await apiClient.rebuildDraftSubtitles(
        draftId,
        renderFlags
          ? {
              render_flags: {
                transitions: renderFlags.transitions,
                stabilize: renderFlags.stabilize,
                subtitles: renderFlags.subtitles,
                auto_reframe: renderFlags.autoReframe,
                smart_camera: renderFlags.smartCamera,
              },
            }
          : undefined,
      );
      setEdited(false);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err);
        onRebuildError?.(`用新字幕更新成品失敗：${msg}`);
    } finally {
      setRebuilding(false);
    }
  }, [draftId, onRebuildStart, onRebuildError, renderFlags]);

  const total = cues.length;
  const charCount = useMemo(
    () => cues.reduce((acc, c) => acc + c.text.length, 0),
    [cues],
  );

  if (loading) {
    return (
      <section className="sub-editor">
        <h3 className="sub-editor__title">字幕編輯</h3>
        <p className="sub-editor__loading mono">載入中…</p>
      </section>
    );
  }

  if (loadError) {
    return (
      <section className="sub-editor">
        <h3 className="sub-editor__title">字幕編輯</h3>
        <p className="sub-editor__error mono">載入失敗：{loadError}</p>
      </section>
    );
  }

  if (total === 0) {
    return (
      <section className="sub-editor">
        <h3 className="sub-editor__title">字幕編輯</h3>
        <p className="sub-editor__empty mono">這個版本沒有字幕（可能是無語音素材）</p>
      </section>
    );
  }

  return (
    <section className="sub-editor" aria-label="字幕編輯器">
      <header className="sub-editor__head">
        <h3 className="sub-editor__title">字幕編輯</h3>
        <span className="sub-editor__count mono">
          {total} 句 · {charCount} 字
        </span>
      </header>
      {locked ? (
        <p className="sub-editor__hint mono">成品正在製作中，完成後再來編輯字幕</p>
      ) : (
        <p className="sub-editor__hint mono">
          點擊文字直接編輯；改完按下「用新字幕更新成品」會重新產生一版。
        </p>
      )}
      <ol className="sub-editor__list">
        {cues.map((c) => (
          <li key={c.idx} className="sub-cue">
            <div className="sub-cue__time mono">
              <span>{formatTimecode(c.start_ms)}</span>
              <span className="sub-cue__arrow">→</span>
              <span>{formatTimecode(c.end_ms)}</span>
            </div>
            <textarea
              className="sub-cue__text"
              value={c.text}
              rows={2}
              maxLength={400}
              disabled={locked}
              onChange={(e) => handleChange(c.idx, e.currentTarget.value)}
              onBlur={() => handleBlur(c.idx)}
              aria-label={`字幕第 ${c.idx} 句`}
            />
          </li>
        ))}
      </ol>
      <div className="sub-editor__actions">
        <button
          type="button"
          className="cta cta--primary"
          onClick={() => void triggerRebuild()}
          disabled={!edited || locked || rebuilding}
        >
          {rebuilding ? "送出中…" : "用新字幕更新成品"}
        </button>
      </div>
    </section>
  );
}
