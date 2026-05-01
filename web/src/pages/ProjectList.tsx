import type React from "react";
import { Link, useNavigate } from "react-router-dom";
import type { ProjectSummary } from "../api/types";
import { useProjects } from "../hooks";
import "./ProjectList.css";

function formatCreatedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  // Two physical lines (date + time) so neither truncates at narrow column
  // widths; .entry__num-when uses white-space: pre-line to honour the \n.
  return `${yyyy}/${mm}/${dd}\n${hh}:${mi}`;
}

const STATUS_LABEL: Record<string, string> = {
  drafted: "剪輯就緒",
  analyzing: "分析中",
  approved: "成品就緒",
  rejected: "已退回",
  pending: "待處理",
};

function StatusCell({ project }: { project: ProjectSummary }) {
  if (project.status === "drafted" && project.latest_draft_version != null) {
    return (
      <div className="status-cell status-cell--ready">
        <div className="status-line">
          <span className="dot dot--gold" />
          <span className="status-text">
            剪輯 v{project.latest_draft_version} 已就緒
          </span>
        </div>
        <Link to={`/projects/${project.id}/review`} className="cta cta--primary">
          檢視 →
        </Link>
      </div>
    );
  }

  if (project.status === "analyzing") {
    return (
      <div className="status-cell status-cell--processing">
        <div className="status-line">
          <span className="dot dot--processing" />
          <span className="status-text">處理流程執行中</span>
        </div>
        <div className="progress-track" aria-hidden>
          <div className="progress-bar" style={{ width: "55%" }} />
        </div>
      </div>
    );
  }

  if (project.status === "approved") {
    return (
      <div className="status-cell status-cell--approved">
        <div className="status-line">
          <span className="dot dot--up" />
          <span className="status-text">成品就緒</span>
        </div>
        <Link to={`/projects/${project.id}/review`} className="cta cta--quiet">
          開啟 →
        </Link>
      </div>
    );
  }

  return (
    <div className="status-cell">
      <div className="status-line">
        <span className="dot dot--unknown" />
        <span className="status-text">
          {STATUS_LABEL[project.status] ?? project.status}
        </span>
      </div>
    </div>
  );
}

export default function ProjectList() {
  const { data: projects, error, loading } = useProjects();
  const navigate = useNavigate();
  const list = projects ?? [];

  const goToProject = (projectId: number, ev: React.SyntheticEvent) => {
    // Bail out if the click landed on an interactive child (status-cell CTA
    // <Link> or button) — React Router refuses nested anchors so we render
    // the row as a clickable container, not an anchor itself.
    const target = ev.target as HTMLElement;
    if (target.closest("a, button")) return;
    navigate(`/projects/${projectId}/assets`);
  };

  return (
    <main className="page projects">
      <section className="hero">
        <div className="hero__kicker">
          工作清單 &nbsp;·&nbsp; {loading ? "…" : `${list.length} 件`}
        </div>
        <h1 className="hero__title">
          待<em>檢視</em>的剪輯。
        </h1>
        <p className="hero__lede">
          每一件代表一個專案 ── AI 已將原始素材剪成短片。
          定剪任一剪輯，即會送入你的剪輯軟體。
        </p>
        <div className="hero__actions">
          <Link to="/projects/new" className="cta cta--primary cta--new">
            新增專案 +
          </Link>
        </div>
      </section>

      <section className="board">
        <div className="board__columns" aria-hidden>
          <span>編號</span>
          <span>專案</span>
          <span>狀態</span>
        </div>

        {error && (
          <div className="board__notice" role="alert">
            <span className="mono">API 錯誤 · {error.message}</span>
          </div>
        )}

        {loading && !projects && (
          <div className="board__notice">
            <span className="mono">載入中…</span>
          </div>
        )}

        {!loading && projects && list.length === 0 && (
          <div className="board__notice">
            <span className="mono">目前沒有專案</span>
          </div>
        )}

        <ol className="board__list">
          {list.map((p, i) => (
            <li
              key={p.id}
              className="entry entry--clickable"
              role="link"
              tabIndex={0}
              aria-label={`開啟專案 ${p.name}`}
              onClick={(e) => goToProject(p.id, e)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  goToProject(p.id, e);
                }
              }}
              style={{ animationDelay: `${100 + i * 90}ms` }}
            >
              <div className="entry__num">
                <div className="entry__num-fig">
                  {String(p.id).padStart(3, "0")}
                </div>
                <div className="entry__num-when">
                  {formatCreatedAt(p.created_at)}
                </div>
              </div>

              <div className="entry__body">
                <div className="entry__client">{p.client ?? "自由案件"}</div>
                <h2 className="entry__name">{p.name}</h2>
                <div className="entry__meta">
                  <span>{p.asset_count} 個素材</span>
                  <span className="entry__meta-sep">·</span>
                  <span className="mono">風格檔 {p.profile_name}</span>
                </div>
              </div>

              <StatusCell project={p} />
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
