import type { DraftTrustReport, TrustDegradationEvent, TrustSummary } from "../api/types";
import "./TrustReportBanner.css";

interface TrustReportBannerProps {
  summary?: TrustSummary | null;
  report?: DraftTrustReport | null;
  compact?: boolean;
}

const STATUS_LABEL: Record<string, string> = {
  planned: "已依計畫完成",
  degraded: "可用，但有降級",
  failed: "生成失敗",
  unknown: "信任狀態未知",
};

const STATUS_COPY: Record<string, string> = {
  planned: "這版完成了要求的規劃、字幕、音訊與輸出階段。",
  degraded: "這版可以檢查或匯出，但部分階段使用 fallback；核准前請先看原因。",
  failed: "必要階段失敗，這版不能視為可用輸出。",
  unknown: "這版產生於信任報告之前，沒有詳細製作證據。",
};

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    plan: "剪輯規劃",
    plan_generation: "AI 規劃",
    frame_analysis: "逐幀分析",
    stabilization: "防抖素材",
    tracking: "追蹤/構圖",
    smart_camera: "Smart Camera",
    story_tts: "旁白 TTS",
    audio_mix: "音訊混音",
    bgm_mix: "BGM 混音",
    render_output: "影片輸出",
  };
  return labels[stage] ?? stage;
}

function metricValue(value: unknown): string {
  if (value == null) return "無";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return Number.isInteger(value) ? `${value}` : value.toFixed(3);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function groupEvents(events: TrustDegradationEvent[]): [string, TrustDegradationEvent[]][] {
  const groups = new Map<string, TrustDegradationEvent[]>();
  for (const event of events) {
    groups.set(event.stage, [...(groups.get(event.stage) ?? []), event]);
  }
  return [...groups.entries()];
}

export default function TrustReportBanner({ summary, report, compact }: TrustReportBannerProps) {
  const status = summary?.status ?? "unknown";
  const events = report?.degradation_events ?? [];
  const count = summary?.degradation_count ?? events.length;
  return (
    <section className={`trust-banner trust-banner--${status}`} aria-label="生成可信度">
      <div className="trust-banner__main">
        <span className="trust-banner__label">{STATUS_LABEL[status]}</span>
        <span className="trust-banner__copy">
          {status === "degraded" && count > 0
            ? `${STATUS_COPY[status]} 共 ${count} 項。`
            : STATUS_COPY[status]}
        </span>
      </div>
      {!compact && events.length > 0 && (
        <details className="trust-banner__details">
          <summary>查看降級原因</summary>
          {groupEvents(events).map(([stage, items]) => (
            <div className="trust-banner__stage" key={stage}>
              <strong>{stageLabel(stage)}</strong>
              {items.map((event) => (
                <div className="trust-banner__event" key={`${event.stage}:${event.code}:${event.message}`}>
                  <p>{event.message}</p>
                  {event.fallback_used && <span className="mono">fallback: {event.fallback_used}</span>}
                  {event.evidence.length > 0 && (
                    <ul>
                      {event.evidence.map((metric) => (
                        <li key={metric.name}>
                          <span className="mono">{metric.name}</span>: {metric.available ? metricValue(metric.value) : (metric.message ?? "無法取得")}
                          {metric.unit ? ` ${metric.unit}` : ""}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
