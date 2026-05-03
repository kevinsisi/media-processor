import { useEffect, useMemo, useRef } from "react";
import type { AssetDetail, DraftSegmentOut } from "../../api/types";
import { apiClient } from "../../api/client";
import TransportControls from "./TransportControls";
import "./PreviewPane.css";

// Native <video> preview that scrubs source assets directly. The
// playhead's on-timeline ms maps to a (segment, asset-time) pair; we
// switch the <video>.src whenever the playhead crosses a segment
// boundary.
//
// While playing, ``timeupdate`` events drive the playhead forward; when
// the asset-time crosses the current segment's asset_end_ms, we jump
// to the next segment's start (both video.src AND video.currentTime
// switch). Pause is the user's responsibility (Space / button).

export interface PreviewPaneProps {
  segments: DraftSegmentOut[];
  assetsById: Record<number, AssetDetail>;
  /** On-timeline ms — single source of truth, owned by the page. */
  playheadMs: number;
  onPlayheadMsChange: (ms: number) => void;
  totalMs: number;
  isPlaying: boolean;
  onIsPlayingChange: (v: boolean) => void;
  speed: number;
  onSpeedChange: (v: number) => void;
}

export default function PreviewPane({
  segments,
  assetsById,
  playheadMs,
  onPlayheadMsChange,
  totalMs,
  isPlaying,
  onIsPlayingChange,
  speed,
  onSpeedChange,
}: PreviewPaneProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  // We intentionally don't put currentSrc in React state — switching
  // <video>.src remounts the media element, and we want it imperative.
  const lastAppliedSrcRef = useRef<string | null>(null);
  // Distinguish a programmatic ``video.currentTime = X`` write (which
  // we trigger when a segment boundary forces a jump) from a natural
  // playback advance — the timeupdate handler suppresses the next
  // event after a programmatic seek to avoid a feedback loop with the
  // playhead state.
  const suppressNextUpdateRef = useRef<boolean>(false);

  const orderedSegments = useMemo(
    () => [...segments].sort((a, b) => a.order - b.order),
    [segments],
  );

  const activeSegment = useMemo(() => {
    if (orderedSegments.length === 0) return null;
    for (const s of orderedSegments) {
      if (
        playheadMs >= s.on_timeline_start_ms &&
        playheadMs < s.on_timeline_end_ms
      ) {
        return s;
      }
    }
    // Past the end → clamp to last segment (so the video shows its
    // final frame instead of black).
    const last = orderedSegments[orderedSegments.length - 1];
    return playheadMs >= last.on_timeline_end_ms ? last : orderedSegments[0];
  }, [orderedSegments, playheadMs]);

  const activeAsset =
    activeSegment?.asset_id != null ? assetsById[activeSegment.asset_id] : null;
  const activeSrc = activeAsset ? apiClient.assetVideoUrl(activeAsset) : null;

  // Apply src + currentTime imperatively whenever activeSegment / playhead
  // changes. React's controlled `<video src>` doesn't help here because
  // we want to avoid a remount when only currentTime changes.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !activeSegment || activeSegment.asset_start_ms == null)
      return;
    const desiredAssetMs =
      activeSegment.asset_start_ms +
      (playheadMs - activeSegment.on_timeline_start_ms);
    if (activeSrc && lastAppliedSrcRef.current !== activeSrc) {
      lastAppliedSrcRef.current = activeSrc;
      video.src = activeSrc;
      // Newly-loaded media needs to reach HAVE_METADATA before
      // currentTime takes effect; do it on loadedmetadata.
      const onMeta = () => {
        suppressNextUpdateRef.current = true;
        video.currentTime = desiredAssetMs / 1000;
        if (isPlaying) void video.play().catch(() => {});
        video.removeEventListener("loadedmetadata", onMeta);
      };
      video.addEventListener("loadedmetadata", onMeta);
      return () => video.removeEventListener("loadedmetadata", onMeta);
    }
    // Same src — only seek if the difference is meaningful (>50 ms),
    // to avoid fighting the timeupdate-driven playhead during play.
    const delta = Math.abs(video.currentTime * 1000 - desiredAssetMs);
    if (delta > 50) {
      suppressNextUpdateRef.current = true;
      video.currentTime = desiredAssetMs / 1000;
    }
  }, [activeSegment, activeSrc, playheadMs, isPlaying]);

  // Drive playbackRate.
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed]);

  // Play/pause sync.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (isPlaying) {
      void video.play().catch(() => onIsPlayingChange(false));
    } else {
      video.pause();
    }
  }, [isPlaying, onIsPlayingChange]);

  // timeupdate → advance playhead. Crossing a segment boundary is
  // detected via asset-time exceeding the segment's asset_end_ms; we
  // jump to the next segment's start (or pause at the end).
  const handleTimeUpdate = () => {
    if (suppressNextUpdateRef.current) {
      suppressNextUpdateRef.current = false;
      return;
    }
    const video = videoRef.current;
    if (!video || !activeSegment || activeSegment.asset_start_ms == null)
      return;
    const assetMs = video.currentTime * 1000;
    if (
      activeSegment.asset_end_ms != null &&
      assetMs >= activeSegment.asset_end_ms
    ) {
      // Reached the end of this segment. Find the next one.
      const idx = orderedSegments.indexOf(activeSegment);
      const next = orderedSegments[idx + 1] ?? null;
      if (next) {
        onPlayheadMsChange(next.on_timeline_start_ms);
      } else {
        // End of timeline — pause + park playhead at totalMs.
        onIsPlayingChange(false);
        onPlayheadMsChange(totalMs);
      }
      return;
    }
    const newPlayheadMs =
      activeSegment.on_timeline_start_ms +
      (assetMs - activeSegment.asset_start_ms);
    onPlayheadMsChange(newPlayheadMs);
  };

  return (
    <div className="preview-pane">
      <div className="preview-pane__viewport">
        {activeSrc ? (
          <video
            ref={videoRef}
            className="preview-pane__video"
            playsInline
            muted={false}
            controls={false}
            preload="metadata"
            onTimeUpdate={handleTimeUpdate}
            onEnded={() => onIsPlayingChange(false)}
          />
        ) : (
          <div className="preview-pane__empty">無可預覽的素材</div>
        )}
      </div>
      <TransportControls
        isPlaying={isPlaying}
        onTogglePlay={() => onIsPlayingChange(!isPlaying)}
        currentMs={playheadMs}
        totalMs={totalMs}
        speed={speed}
        onSpeedChange={onSpeedChange}
      />
    </div>
  );
}
