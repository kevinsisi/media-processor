import { Link } from "react-router-dom";
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
  return `${yyyy}·${mm}·${dd} · ${hh}:${mi}`;
}

function StatusCell({ project }: { project: ProjectSummary }) {
  if (project.status === "drafted" && project.latest_draft_version != null) {
    return (
      <div className="status-cell status-cell--ready">
        <div className="status-line">
          <span className="dot dot--gold" />
          <span className="status-text">
            draft v{project.latest_draft_version} ready
          </span>
        </div>
        <Link to={`/projects/${project.id}/review`} className="cta cta--primary">
          Review →
        </Link>
      </div>
    );
  }

  if (project.status === "analyzing") {
    return (
      <div className="status-cell status-cell--processing">
        <div className="status-line">
          <span className="dot dot--processing" />
          <span className="status-text">pipeline running</span>
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
          <span className="status-text">approved</span>
        </div>
        <Link to={`/projects/${project.id}/review`} className="cta cta--quiet">
          Open →
        </Link>
      </div>
    );
  }

  return (
    <div className="status-cell">
      <div className="status-line">
        <span className="dot dot--unknown" />
        <span className="status-text">{project.status}</span>
      </div>
    </div>
  );
}

export default function ProjectList() {
  const { data: projects, error, loading } = useProjects();
  const list = projects ?? [];

  return (
    <main className="page projects">
      <section className="hero">
        <div className="hero__kicker">
          OPEN BOARD &nbsp;·&nbsp; {loading ? "…" : `${list.length} ISSUES`}
        </div>
        <h1 className="hero__title">
          Issues, in <em>review</em>.
        </h1>
        <p className="hero__lede">
          Each issue is a project of raw footage AI has cut into a
          short. Approve a draft and it lands in your editor.
        </p>
      </section>

      <section className="board">
        <div className="board__columns" aria-hidden>
          <span>№</span>
          <span>Project</span>
          <span>Status</span>
        </div>

        {error && (
          <div className="board__notice" role="alert">
            <span className="mono">api error · {error.message}</span>
          </div>
        )}

        {loading && !projects && (
          <div className="board__notice">
            <span className="mono">loading…</span>
          </div>
        )}

        {!loading && projects && list.length === 0 && (
          <div className="board__notice">
            <span className="mono">no projects yet</span>
          </div>
        )}

        <ol className="board__list">
          {list.map((p, i) => (
            <li
              key={p.id}
              className="entry"
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
                <div className="entry__client">{p.client ?? "freelance"}</div>
                <h2 className="entry__name">{p.name}</h2>
                <div className="entry__meta">
                  <span>{p.asset_count} 素材</span>
                  <span className="entry__meta-sep">·</span>
                  <span className="mono">profile {p.profile_name}</span>
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
