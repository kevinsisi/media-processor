import { Link } from "react-router-dom";
import { MOCK_PROJECTS, type MockProject } from "../data/mockData";
import "./ProjectList.css";

function StatusCell({ project }: { project: MockProject }) {
  if (project.status === "drafted" && project.draftVersion) {
    return (
      <div className="status-cell status-cell--ready">
        <div className="status-line">
          <span className="dot dot--gold" />
          <span className="status-text">
            draft v{project.draftVersion} ready
            {project.pendingReview ? ` · ${project.pendingReview} pending` : ""}
          </span>
        </div>
        <Link to={`/projects/${project.id}/review`} className="cta cta--primary">
          Review →
        </Link>
      </div>
    );
  }

  if (project.status === "analyzing" && project.pipelineStage) {
    const { stage, total, label } = project.pipelineStage;
    const pct = (stage / total) * 100;
    return (
      <div className="status-cell status-cell--processing">
        <div className="status-line">
          <span className="dot dot--processing" />
          <span className="status-text">
            pipeline · stage {stage}/{total} {label}
          </span>
        </div>
        <div className="progress-track" aria-hidden>
          <div className="progress-bar" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  if (project.status === "approved") {
    return (
      <div className="status-cell status-cell--approved">
        <div className="status-line">
          <span className="dot dot--up" />
          <span className="status-text">approved · downloaded</span>
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
  return (
    <main className="page projects">
      <section className="hero">
        <div className="hero__kicker">
          OPEN BOARD &nbsp;·&nbsp; {MOCK_PROJECTS.length} ISSUES
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

        <ol className="board__list">
          {MOCK_PROJECTS.map((p, i) => (
            <li
              key={p.id}
              className="entry"
              style={{ animationDelay: `${100 + i * 90}ms` }}
            >
              <div className="entry__num">
                <div className="entry__num-fig">{p.number}</div>
                <div className="entry__num-when">{p.createdAt}</div>
              </div>

              <div className="entry__body">
                <div className="entry__client">
                  {p.client === "carsmeet" ? "carsmeet" : "freelance"}
                </div>
                <h2 className="entry__name">{p.name}</h2>
                <div className="entry__meta">
                  <span>{p.assetCount} 素材</span>
                  <span className="entry__meta-sep">·</span>
                  <span className="mono">profile {p.profileName}</span>
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
