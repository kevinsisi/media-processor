import "./RotateHint.css";

// Shown to mobile-portrait users who hit the timeline editor route.
// The editor genuinely doesn't fit in 9:16 viewport — there's no way
// to lay out preview + tracks + inspector in that space — so we ask
// the operator to rotate. Once orientation flips to landscape the
// page re-mounts the canvas (matchMedia listener in TimelineEditor).

export default function RotateHint() {
  return (
    <div className="rotate-hint">
      <div className="rotate-hint__icon" aria-hidden="true">
        ⤺
      </div>
      <h2 className="rotate-hint__title">需要橫向螢幕</h2>
      <p className="rotate-hint__body">
        進階編輯需要更寬的視野，請將裝置轉為橫向。
      </p>
      <p className="rotate-hint__body rotate-hint__body--muted">
        如果旋轉沒有反應，請確認系統「自動旋轉」沒有被鎖定。
      </p>
    </div>
  );
}
