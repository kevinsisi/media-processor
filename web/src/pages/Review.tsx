import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  MOCK_DRAFT,
  TAG_DISPLAY,
  findProject,
  type MockSegment,
  type SegmentTag,
} from "../data/mockData";
import "./Review.css";

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
  onClose,
  onSubmit,
}: {
  open: boolean;
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
          <button className="cta cta--quiet" onClick={onClose}>
            Cancel
          </button>
          <button
            className="cta cta--primary"
            disabled={!text.trim()}
            onClick={() => {
              onSubmit(text);
              onClose();
            }}
          >
            Re-cut →
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Review() {
  const { id } = useParams<{ id: string }>();
  const project = id ? findProject(id) : undefined;
  const draft = MOCK_DRAFT;

  const [selected, setSelected] = useState<number>(13);
  const [promptOpen, setPromptOpen] = useState(false);
  const [decision, setDecision] = useState<
    null | "approved" | "rejected" | "regenerating" | "downloaded"
  >(null);

  if (!project) {
    return (
      <main className="page review">
        <p>Project not found.</p>
        <Link to="/">← back</Link>
      </main>
    );
  }

  const selectedSegment: MockSegment | undefined = draft.segments[selected];

  return (
    <main className="page review">
      {/* Top breadcrumb / title strip */}
      <div className="bread">
        <Link to="/" className="bread__back">
          ← Issues
        </Link>
        <div className="bread__title">
          <span className="bread__num">№ {project.number}</span>
          <span className="bread__sep">·</span>
          <span className="bread__client">{project.client}</span>
          <span className="bread__sep">·</span>
          <span className="bread__name">{project.name}</span>
          <span className="bread__sep">·</span>
          <span className="bread__draft">draft v{draft.version}</span>
        </div>
        <div className="bread__score">
          <span className="mono">AI confidence</span>
          <span className="bread__score-fig">{draft.aiScore.toFixed(1)}</span>
          <span className="mono">/ 10</span>
        </div>
      </div>

      {/* Main two-pane: video + intel */}
      <div className="stage">
        <figure className="player" aria-label="video preview">
          <div className="player__frame">
            <div className="player__box">
              <PreviewArtwork />
              <div className="player__overlay">
                <div className="player__time mono">
                  00:{(Math.floor((selectedSegment?.startMs ?? 0) / 1000))
                    .toString()
                    .padStart(2, "0")}
                  &nbsp;/&nbsp; 00:30
                </div>
              </div>
            </div>
          </div>
          <figcaption className="player__caption">
            <span className="mono">9:16 reframe preview</span>
            <span className="mono">·</span>
            <span className="mono">{draft.segments.length} cuts</span>
          </figcaption>
        </figure>

        <aside className="intel">
          <div className="intel__eyebrow">INTEL</div>

          <div className="intel__score">
            <div className="intel__score-fig">{draft.aiScore.toFixed(1)}</div>
            <div className="intel__score-of">/ 10</div>
            <ScoreStars score={draft.aiScore} />
          </div>

          <ul className="intel__list">
            {Object.entries(draft.intel.counts).map(([tag, count]) => {
              const t = TAG_DISPLAY[tag as SegmentTag];
              return (
                <li key={tag} className="intel__row">
                  <span className={`pill pill--${t.tone}`}>{t.short}</span>
                  <span className="intel__row-label">{t.label}</span>
                  <span className="intel__row-count mono">{count}</span>
                </li>
              );
            })}
            <li className="intel__divider" aria-hidden />
            <li className="intel__row intel__row--note">
              <span className="pill pill--warn">F</span>
              <span className="intel__row-label">陌生人臉（未在打馬清單）</span>
              <span className="intel__row-count mono">
                {draft.intel.strangerFacesNotInBlurList}
              </span>
            </li>
            <li className="intel__row intel__row--note">
              <span className="pill pill--quiet">字</span>
              <span className="intel__row-label">字幕行數</span>
              <span className="intel__row-count mono">
                {draft.intel.captionsLines}
              </span>
            </li>
            <li className="intel__row intel__row--note">
              <span className="pill pill--quiet">♪</span>
              <span className="intel__row-label">BPM 對拍切點</span>
              <span className="intel__row-count mono">
                {draft.intel.bpmAlignedCuts}/{draft.beatGridCount}
              </span>
            </li>
          </ul>
        </aside>
      </div>

      {/* Timeline strip */}
      <section className="tl">
        <div className="tl__head">
          <div className="tl__eyebrow">TIMELINE</div>
          <div className="tl__hint mono">click any block · see why AI picked it</div>
        </div>
        <div className="tl__strip" role="list">
          {draft.segments.map((seg, i) => {
            const t = TAG_DISPLAY[seg.tag];
            const widthPct = ((seg.endMs - seg.startMs) / draft.durationMs) * 100;
            const isSel = selected === i;
            return (
              <button
                key={seg.order}
                className={`tl__cell tl__cell--${t.tone}${isSel ? " tl__cell--selected" : ""}`}
                style={{ flex: `${widthPct} 1 0` }}
                onClick={() => setSelected(i)}
                title={`${t.label} · ${seg.score.toFixed(1)}`}
                role="listitem"
                aria-pressed={isSel}
              >
                <span className="tl__cell-letter">{t.short}</span>
              </button>
            );
          })}
        </div>
        <div className="tl__ruler mono">
          <span>0:00</span>
          <span>0:15</span>
          <span>0:30</span>
        </div>
      </section>

      {/* Why-this-clip panel */}
      {selectedSegment && (
        <section className="why">
          <div className="why__head">
            <div className="why__eyebrow">SEGMENT #{selectedSegment.order.toString().padStart(2, "0")}</div>
            <div className="why__time mono">
              {(selectedSegment.startMs / 1000).toFixed(1)}s — {(selectedSegment.endMs / 1000).toFixed(1)}s
              <span className="why__src">· {selectedSegment.assetName}</span>
            </div>
            <div className="why__score">
              <span className="why__score-label mono">total</span>
              <span className="why__score-fig">{selectedSegment.score.toFixed(1)}</span>
              <span className="mono">/ 10</span>
            </div>
          </div>

          <div className="why__body">
            <div className="why__col">
              <div className="why__sub">why AI picked it</div>
              <ul className="why__reasons">
                {selectedSegment.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
            <div className="why__col">
              <div className="why__sub">tag</div>
              <div className={`pill pill--${TAG_DISPLAY[selectedSegment.tag].tone} pill--lg`}>
                {TAG_DISPLAY[selectedSegment.tag].short} &nbsp; {TAG_DISPLAY[selectedSegment.tag].label}
              </div>
              {selectedSegment.beat !== undefined && (
                <div className="why__beat mono">
                  aligned to beat #{selectedSegment.beat}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Actions */}
      <div className="actions">
        <button
          className={`action action--approve${decision === "approved" ? " action--done" : ""}`}
          onClick={() => setDecision("approved")}
        >
          <span className="action__glyph">✓</span>
          <span className="action__label">Approve</span>
          <span className="action__hint mono">→ auto-sync to CapCut</span>
        </button>
        <button
          className={`action${decision === "regenerating" ? " action--done" : ""}`}
          onClick={() => setDecision("regenerating")}
        >
          <span className="action__glyph">↺</span>
          <span className="action__label">Regenerate</span>
          <span className="action__hint mono">re-pick segments, same profile</span>
        </button>
        <button
          className="action action--prompt"
          onClick={() => setPromptOpen(true)}
        >
          <span className="action__glyph">❉</span>
          <span className="action__label">Re-cut with prompt</span>
          <span className="action__hint mono">tell the AI what to change</span>
        </button>
        <button
          className={`action${decision === "downloaded" ? " action--done" : ""}`}
          onClick={() => setDecision("downloaded")}
        >
          <span className="action__glyph">↓</span>
          <span className="action__label">Download zip</span>
          <span className="action__hint mono">manual copy to CapCut</span>
        </button>
        <button
          className={`action action--reject${decision === "rejected" ? " action--done" : ""}`}
          onClick={() => setDecision("rejected")}
        >
          <span className="action__glyph">×</span>
          <span className="action__label">Reject</span>
          <span className="action__hint mono">discard draft v{draft.version}</span>
        </button>
      </div>

      {decision && (
        <div className="toast">
          <span className="mono">
            {decision === "approved" && "✓ approved · syncing to CapCut draft folder"}
            {decision === "regenerating" && "↺ regenerating with same profile…"}
            {decision === "rejected" && "× draft rejected"}
            {decision === "downloaded" && "↓ zip downloaded · move to CapCut folder manually"}
          </span>
          <button className="toast__close" onClick={() => setDecision(null)}>×</button>
        </div>
      )}

      <PromptDialog
        open={promptOpen}
        onClose={() => setPromptOpen(false)}
        onSubmit={() => setDecision("regenerating")}
      />
    </main>
  );
}

function PreviewArtwork() {
  // Layered abstract artwork — evokes a luxury car shot in low key without
  // shipping any actual content. Pure SVG / CSS.
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
      {/* Vehicle silhouette suggested by curves */}
      <path
        d="M -40 460 C 60 380, 110 360, 180 360 C 260 360, 320 400, 410 460 L 410 540 L -40 540 Z"
        fill="url(#bodyhi)"
        opacity="0.95"
      />
      {/* Roof curve */}
      <path
        d="M 80 380 C 130 320, 230 320, 290 380"
        stroke="#2c2820"
        strokeWidth="2"
        fill="none"
        opacity="0.7"
      />
      {/* Wheel arches */}
      <ellipse cx="100" cy="500" rx="42" ry="22" fill="#0e0d0c" />
      <ellipse cx="270" cy="500" rx="42" ry="22" fill="#0e0d0c" />
      <ellipse cx="100" cy="500" rx="22" ry="11" fill="#1a1612" />
      <ellipse cx="270" cy="500" rx="22" ry="11" fill="#1a1612" />
      {/* Light streak — body line highlight */}
      <rect x="40" y="430" width="280" height="2" fill="url(#goldstreak)" />
      {/* Subtle vignette overlay */}
      <radialGradient id="vig" cx="50%" cy="50%" r="80%">
        <stop offset="60%" stopColor="#000" stopOpacity="0" />
        <stop offset="100%" stopColor="#000" stopOpacity="0.6" />
      </radialGradient>
      <rect width="360" height="640" fill="url(#vig)" />
    </svg>
  );
}
