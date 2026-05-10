import { useEffect, useState } from "react";
import "./Health.css";

interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  dependencies: { postgres: "up" | "down"; redis: "up" | "down" };
}

const STATUS_LABEL: Record<HealthResponse["status"], string> = {
  ok: "正常運作",
  degraded: "降級中",
};

const DEPENDENCY_LABEL: Record<"up" | "down", string> = {
  up: "正常",
  down: "離線",
};

function formatRelative(seconds: number): string {
  if (seconds < 1) return "剛才";
  if (seconds < 60) return `${Math.floor(seconds)} 秒前`;
  const m = Math.floor(seconds / 60);
  return `${m} 分鐘前`;
}

export default function Health() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [now, setNow] = useState<number>(Date.now());

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch("/api/health")
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json() as Promise<HealthResponse>;
        })
        .then((data) => {
          if (cancelled) return;
          setHealth(data);
          setError(null);
          setFetchedAt(Date.now());
        })
        .catch((e) => {
          if (cancelled) return;
          setError(e instanceof Error ? e.message : String(e));
        });
    };
    load();
    const refresh = setInterval(load, 15000);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      cancelled = true;
      clearInterval(refresh);
      clearInterval(tick);
    };
  }, []);

  const fetchedAgo = fetchedAt ? (now - fetchedAt) / 1000 : null;
  const statusKey = health?.status ?? (error ? "degraded" : null);

  return (
    <main className="health">
      <div className="rule-left" aria-hidden />

      <header className="masthead">
        <div className="kicker">
          系統狀態 &nbsp;·&nbsp; 15 秒自動更新
        </div>
        <h1 className="title">
          Media <span className="title-em">·</span> Processor
        </h1>
        <div className="subtitle">
          API、資料庫與佇列基礎服務檢查
        </div>
      </header>

      <section className="block" style={{ animationDelay: "120ms" }}>
        <div className="eyebrow">系統狀態</div>
        <div className="row">
          <span className="row-key">系統</span>
          <span className="leader" aria-hidden />
          <span
            className={`row-val row-val--accent status status--${statusKey ?? "unknown"}`}
          >
            {error
              ? "無法連線"
              : statusKey
                ? STATUS_LABEL[statusKey as HealthResponse["status"]]
                : "檢查中…"}
          </span>
        </div>
        <div className="row">
          <span className="row-key">版本</span>
          <span className="leader" aria-hidden />
          <span className="row-val mono">{health?.version ?? "—"}</span>
        </div>
      </section>

      <section className="block" style={{ animationDelay: "260ms" }}>
        <div className="eyebrow">相依服務</div>
        <div className="row">
          <span className="row-key mono">postgres</span>
          <span className="leader" aria-hidden />
          <span
            className={`row-val mono status status--${health?.dependencies.postgres ?? "unknown"}`}
          >
            {health ? DEPENDENCY_LABEL[health.dependencies.postgres] : "—"}
          </span>
        </div>
        <div className="row">
          <span className="row-key mono">redis</span>
          <span className="leader" aria-hidden />
          <span
            className={`row-val mono status status--${health?.dependencies.redis ?? "unknown"}`}
          >
            {health ? DEPENDENCY_LABEL[health.dependencies.redis] : "—"}
          </span>
        </div>
      </section>

      <footer className="colophon" style={{ animationDelay: "420ms" }}>
        <div className="hairline" aria-hidden />
        <div className="meta">
          {error ? (
            <span className="meta-error">服務錯誤 · {error}</span>
          ) : fetchedAgo === null ? (
            <span>查詢中…</span>
          ) : (
            <span>{formatRelative(fetchedAgo)}更新</span>
          )}
        </div>
        <div className="meta meta-right">
          <span className="mono">
            <a href="/api/health" target="_blank" rel="noreferrer">
              /api/health
            </a>
          </span>
        </div>
      </footer>
    </main>
  );
}
