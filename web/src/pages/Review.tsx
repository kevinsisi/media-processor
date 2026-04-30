import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type {
  DraftDetail,
  DraftSegmentOut,
  DraftSummary,
  ProjectDetail,
  ReviewAction,
} from "../api/types";
import {
  useDraft,
  useProject,
  useProjectDrafts,
  useReviewMutation,
} from "../hooks";
import "./Review.css";

// Deterministic decorative tone for a segment block. The API does not yet
// expose per-segment tags / scores (M3 work), so the timeline cycles through
// tones based on order to keep visual variety without faking semantic data.
const TONE_CYCLE = ["gold", "hero", "wheel", "body", "interior"] as const;
function toneFor(order: number): (typeof TONE_CYCLE)[number] {
  return TONE_CYCLE[order % TONE_CYCLE.length];
}

function ScoreStars({ score }: { score: number }) {
  const filled = Math.round((score / 10) * 5);
  return (
    <span className="stars" aria-label={`${score} out of 10`}>
      {"★★★★★".slice(0, filled)}
      <span className="stars__empty">{"☆☆☆☆☆".slice(0, 5 - filled)}</span>
    </span>
  );
}

function PromptDialog({
  open,
  busy,
  onClose,
  onSubmit,
}: {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  if (!open) return null;
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__eyebrow">RE-CUT WITH PROMPT</div>
        <h3 className="modal__title">
          Tell the AI <em>what to change</em>.
        </h3>
        <p className="modal__lede">
          Plain text — describe the cut you want. The model adjusts profile
          weights and re-runs cut planning. Costs a few NT$ in API.
        </p>
        <textarea
          className="modal__input"
          placeholder="多用車身特寫，開頭要 Hero shot，少用輪框…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={5}
          autoFocus
        />
        <div className="modal__actions">
          <button className="cta cta--quiet" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="cta cta--primary"
            disabled={!text.trim() || busy}
            onClick={() => onSubmit(text)}
          >
            {busy ? "Re-cutting…" : "Re-cut →"}
          </button>
        </div>
      </div>
    </div>
  );
}

function pickLatestDraft(drafts: DraftSummary[] | null): DraftSummary | null {
  if (!drafts || drafts.length === 0) return null;
  return drafts.reduce((acc, d) => (d.version > acc.version ? d : acc));
}

interface ReviewBodyProps {
  project: ProjectDetail;
  draft: DraftDetail;
}

function ReviewBody({ project, draft }: ReviewBodyProps) {
  const segments = draft.segments;
  const durationMs = useMemo(() => {
    if (segments.length === 0) return 0;
    return Math.max(...segments.map((s) => s.on_timeline_end_ms));
  }, [segments]);

  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [promptOpen, setPromptOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<ReviewAction | null>(null);

  const review = useReviewMutation();

  const submitAction = async (
    action: ReviewAction,
    promptFeedback?: string,
  ) => {
    setPendingAction(action);
    try {
      await review.submit({
        draft_id: draft.id,
        action,
        prompt_feedback: promptFeedback ?? null,
      });
    } catch {
      // surface via review.error → toast
    } finally {
      setPendingAction(null);
    }
  };

  const aiScore = draft.ai_score ?? 0;
  const selectedSegment: DraftSegmentOut | undefined = segments[selectedIdx];

  return (
    <>
      <div className="bread">
        <Link to="/" className="bread__back">
          ← Issues
        </Link>
        <div className="bread__title">
          <span className="bread__num">№ {String(project.id).padStart(3, "0")}</span>
          <span className="bread__sep">·</span>
          <span className="bread__client">{project.client ?? "freelance"}</span>
          <span className="bread__sep">·</span>
          <span className="bread__name">{project.name}</span>
          <span className="bread__sep">·</span>
          <span className="bread__draft">draft v{draft.version}</span>
        </div>
        <div className="bread__score">
          <span className="mono">AI confidence</span>
          <span className="bread__score-fig">{aiScore.toFixed(1)}</span>
          <span className="mono">/ 10</span>
        </div>
      </div>

      <div className="stage">
        <figure className="player" aria-label="video preview">
          <div className="player__frame">
            <div className="player__box">
              <PreviewArtwork />
              <div className="player__overlay">
                <div className="player__time mono">
                  00:{Math.floor((selectedSegment?.on_timeline_start_ms ?? 0) / 1000)
                    .toString()
                    .padStart(2, "0")}
                  &nbsp;/&nbsp; 00:{Math.floor(durationMs / 1000)
                    .toString()
                    .padStart(2, "0")}
                </div>
              </div>
            </div>
          </div>
          <figcaption className="player__caption">
            <span className="mono">9:16 reframe preview</span>
            <span className="mono">·</span>
            <span className="mono">{segments.length} cuts</span>
          </figcaption>
        </figure>

        <aside className="intel">
          <div className="intel__eyebrow">DRAFT</div>

          <div className="intel__score">
            <div className="intel__score-fig">{aiScore.toFixed(1)}</div>
            <div className="intel__score-of">/ 10</div>
            <ScoreStars score={aiScore} />
          </div>

          <ul className="intel__list">
            <li className="intel__row">
              <span className="pill pill--quiet">v</span>
              <span className="intel__row-label">draft version</span>
              <span className="intel__row-count mono">{draft.version}</span>
            </li>
            <li className="intel__row">
              <span className="pill pill--quiet">#</span>
              <span className="intel__row-label">cuts</span>
              <span className="intel__row-count mono">{segments.length}</span>
            </li>
            <li className="intel__row">
              <span className="pill pill--quiet">⏱</span>
              <span className="intel__row-label">duration</span>
              <span className="intel__row-count mono">
                {(durationMs / 1000).toFixed(1)}s
              </span>
            </li>
            <li className="intel__row">
              <span className="pill pill--quiet">P</span>
              <span className="intel__row-label">profile</span>
              <span className="intel__row-count mono">{draft.profile_name}</span>
            </li>
            <li className="intel__row">
              <span className="pill pill--quiet">S</span>
              <span className="intel__row-label">status</span>
              <span className="intel__row-count mono">{draft.status}</span>
            </li>
          </ul>
        </aside>
      </div>

      <section className="tl">
        <div className="tl__head">
          <div className="tl__eyebrow">TIMELINE</div>
          <div className="tl__hint mono">click any block · see segment timing</div>
        </div>
        <div className="tl__strip" role="list">
          {segments.map((seg, i) => {
            const tone = toneFor(seg.order);
            const segDuration = seg.on_timeline_end_ms - seg.on_timeline_start_ms;
            const widthPct = durationMs > 0 ? (segDuration / durationMs) * 100 : 0;
            const isSel = selectedIdx === i;
            return (
              <button
                key={seg.order}
                className={`tl__cell tl__cell--${tone}${isSel ? " tl__cell--selected" : ""}`}
                style={{ flex: `${widthPct} 1 0` }}
                onClick={() => setSelectedIdx(i)}
                title={`segment #${seg.order} · ${(segDuration / 1000).toFixed(1)}s`}
                role="listitem"
                aria-pressed={isSel}
              >
                <span className="tl__cell-letter">{seg.order + 1}</span>
              </button>
            );
          })}
        </div>
        <div className="tl__ruler mono">
          <span>0:00</span>
          <span>0:{Math.floor(durationMs / 2000).toString().padStart(2, "0")}</span>
          <span>0:{Math.floor(durationMs / 1000).toString().padStart(2, "0")}</span>
        </div>
      </section>

      {selectedSegment && (
        <section className="why">
          <div className="why__head">
            <div className="why__eyebrow">
              SEGMENT #{selectedSegment.order.toString().padStart(2, "0")}
            </div>
            <div className="why__time mono">
              {(selectedSegment.on_timeline_start_ms / 1000).toFixed(1)}s —{" "}
              {(selectedSegment.on_timeline_end_ms / 1000).toFixed(1)}s
              <span className="why__src">
                · asset_segment {selectedSegment.asset_segment_id}
              </span>
            </div>
            <div className="why__score">
              <span className="why__score-label mono">transition</span>
              <span className="why__score-fig">
                {selectedSegment.transition ?? "—"}
              </span>
            </div>
          </div>
        </section>
      )}

      <div className="actions">
        <button
          className={`action action--approve${review.result?.action === "approve" ? " action--done" : ""}`}
          disabled={review.submitting}
          onClick={() => submitAction("approve")}
        >
          <span className="action__glyph">✓</span>
          <span className="action__label">
            {pendingAction === "approve" ? "Approving…" : "Approve"}
          </span>
          <span className="action__hint mono">→ auto-sync to CapCut</span>
        </button>
        <button
          className={`action${review.result?.action === "repatch" && !review.result?.prompt_feedback ? " action--done" : ""}`}
          disabled={review.submitting}
          onClick={() => submitAction("repatch")}
        >
          <span className="action__glyph">↺</span>
          <span className="action__label">
            {pendingAction === "repatch" && !promptOpen
              ? "Regenerating…"
              : "Regenerate"}
          </span>
          <span className="action__hint mono">re-pick segments, same profile</span>
        </button>
        <button
          className="action action--prompt"
          disabled={review.submitting}
          onClick={() => setPromptOpen(true)}
        >
          <span className="action__glyph">❉</span>
          <span className="action__label">Re-cut with prompt</span>
          <span className="action__hint mono">tell the AI what to change</span>
        </button>
        <button
          className={`action${review.result?.action === "download" ? " action--done" : ""}`}
          disabled={review.submitting}
          onClick={() => submitAction("download")}
        >
          <span className="action__glyph">↓</span>
          <span className="action__label">
            {pendingAction === "download" ? "Logging…" : "Download zip"}
          </span>
          <span className="action__hint mono">manual copy to CapCut</span>
        </button>
        <button
          className={`action action--reject${review.result?.action === "reject" ? " action--done" : ""}`}
          disabled={review.submitting}
          onClick={() => submitAction("reject")}
        >
          <span className="action__glyph">×</span>
          <span className="action__label">
            {pendingAction === "reject" ? "Rejecting…" : "Reject"}
          </span>
          <span className="action__hint mono">discard draft v{draft.version}</span>
        </button>
      </div>

      {(review.result || review.error) && (
        <div className="toast">
          <span className="mono">
            {review.error
              ? `× api error · ${review.error.message}`
              : review.result?.action === "approve"
                ? "✓ approved · syncing to CapCut draft folder"
                : review.result?.action === "repatch"
                  ? "↺ repatch queued"
                  : review.result?.action === "reject"
                    ? "× draft rejected"
                    : "↓ download recorded · move zip to CapCut folder manually"}
          </span>
          <button className="toast__close" onClick={review.reset}>
            ×
          </button>
        </div>
      )}

      <PromptDialog
        open={promptOpen}
        busy={review.submitting}
        onClose={() => setPromptOpen(false)}
        onSubmit={async (text) => {
          await submitAction("repatch", text);
          setPromptOpen(false);
        }}
      />
    </>
  );
}

export default function Review() {
  const { id } = useParams<{ id: string }>();
  const projectId = id ? Number(id) : null;

  const projectQ = useProject(projectId);
  const draftsQ = useProjectDrafts(projectId);

  const latestDraftSummary = pickLatestDraft(draftsQ.data);
  const draftQ = useDraft(latestDraftSummary?.id ?? null);

  if (projectId == null || Number.isNaN(projectId)) {
    return (
      <main className="page review">
        <p>Invalid project id.</p>
        <Link to="/">← back</Link>
      </main>
    );
  }

  const error = projectQ.error ?? draftsQ.error ?? draftQ.error;
  if (error) {
    return (
      <main className="page review">
        <div className="bread">
          <Link to="/" className="bread__back">
            ← Issues
          </Link>
        </div>
        <p className="mono" role="alert">
          api error · {error.message}
        </p>
      </main>
    );
  }

  if (projectQ.loading || draftsQ.loading || draftQ.loading) {
    return (
      <main className="page review">
        <div className="bread">
          <Link to="/" className="bread__back">
            ← Issues
          </Link>
        </div>
        <p className="mono">loading…</p>
      </main>
    );
  }

  if (!projectQ.data) {
    return (
      <main className="page review">
        <p>Project not found.</p>
        <Link to="/">← back</Link>
      </main>
    );
  }

  if (!draftQ.data) {
    return (
      <main className="page review">
        <div className="bread">
          <Link to="/" className="bread__back">
            ← Issues
          </Link>
          <div className="bread__title">
            <span className="bread__name">{projectQ.data.name}</span>
          </div>
        </div>
        <p className="mono">no draft yet for this project.</p>
      </main>
    );
  }

  return (
    <main className="page review">
      <ReviewBody project={projectQ.data} draft={draftQ.data} />
    </main>
  );
}

function PreviewArtwork() {
  return (
    <svg
      viewBox="0 0 360 640"
      preserveAspectRatio="xMidYMid slice"
      className="player__art"
      role="img"
      aria-label="luxury car preview placeholder"
    >
      <defs>
        <radialGradient id="floorlight" cx="50%" cy="0%" r="80%">
          <stop offset="0%" stopColor="#3a3328" stopOpacity="0.85" />
          <stop offset="60%" stopColor="#1a1612" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#0e0d0c" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="bodyhi" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#2a2520" />
          <stop offset="40%" stopColor="#5e503a" />
          <stop offset="70%" stopColor="#171411" />
          <stop offset="100%" stopColor="#0e0d0c" />
        </linearGradient>
        <linearGradient id="goldstreak" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#c9a961" stopOpacity="0" />
          <stop offset="50%" stopColor="#e8c882" stopOpacity="0.65" />
          <stop offset="100%" stopColor="#c9a961" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="360" height="640" fill="url(#floorlight)" />
      <path
        d="M -40 460 C 60 380, 110 360, 180 360 C 260 360, 320 400, 410 460 L 410 540 L -40 540 Z"
        fill="url(#bodyhi)"
        opacity="0.95"
      />
      <path
        d="M 80 380 C 130 320, 230 320, 290 380"
        stroke="#2c2820"
        strokeWidth="2"
        fill="none"
        opacity="0.7"
      />
      <ellipse cx="100" cy="500" rx="42" ry="22" fill="#0e0d0c" />
      <ellipse cx="270" cy="500" rx="42" ry="22" fill="#0e0d0c" />
      <ellipse cx="100" cy="500" rx="22" ry="11" fill="#1a1612" />
      <ellipse cx="270" cy="500" rx="22" ry="11" fill="#1a1612" />
      <rect x="40" y="430" width="280" height="2" fill="url(#goldstreak)" />
      <radialGradient id="vig" cx="50%" cy="50%" r="80%">
        <stop offset="60%" stopColor="#000" stopOpacity="0" />
        <stop offset="100%" stopColor="#000" stopOpacity="0.6" />
      </radialGradient>
      <rect width="360" height="640" fill="url(#vig)" />
    </svg>
  );
}
