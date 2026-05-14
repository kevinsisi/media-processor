import "./StickyGenerateFooter.css";

export type FooterState =
  | "idle"       // showInitial or showFallback, analysis complete
  | "blocked"    // showInitial or showFallback, analysis still running
  | "triggering" // request in-flight or showProcessing
  | "queued"     // showQueued
  | "failed"     // showFailed
  | "ready";     // showReady — re-generate action

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
