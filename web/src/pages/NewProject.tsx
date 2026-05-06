import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiClient } from "../api/client";
import type { TargetAspectRatio } from "../api/types";
import "./NewProject.css";

const PROFILE_OPTIONS = [
  { value: "universal", label: "通用短影音" },
  { value: "carsmeet-luxury", label: "Carsmeet 豪車" },
];

const RATIO_OPTIONS: Array<{
  value: TargetAspectRatio;
  label: string;
  hint: string;
  ratio: number; // width / height
}> = [
  { value: "9:16", label: "9 : 16", hint: "Reels · 直式短片", ratio: 9 / 16 },
  { value: "4:5", label: "4 : 5", hint: "貼文牆 · 直式貼文", ratio: 4 / 5 },
  { value: "1:1", label: "1 : 1", hint: "貼文牆 · 方形貼文", ratio: 1 },
];

export default function NewProject() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [profile, setProfile] = useState(PROFILE_OPTIONS[0]?.value ?? "");
  const [ratio, setRatio] = useState<TargetAspectRatio>("9:16");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim().length > 0 && profile.length > 0 && !submitting;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const project = await apiClient.createProject({
        name: name.trim(),
        client: client.trim() || null,
        profile_name: profile,
        target_aspect_ratio: ratio,
      });
      navigate(`/projects/${project.id}/upload`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "建立失敗");
      setSubmitting(false);
    }
  }

  return (
    <main className="page new-project">
      <section className="hero">
        <div className="hero__kicker">新增專案</div>
        <h1 className="hero__title">
          建立一支<em>短影音</em>專案。
        </h1>
        <p className="hero__lede">
          輸入名稱、選定影片風格與成品比例。建立後即可上傳影片與腳本。
        </p>
      </section>

      <form className="np-form" onSubmit={handleSubmit} noValidate>
        <label className="np-field">
          <span className="np-field__label">專案名稱</span>
          <input
            className="np-field__input"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例：六月新車試駕"
            maxLength={255}
            autoComplete="off"
            inputMode="text"
            required
          />
        </label>

        <label className="np-field">
          <span className="np-field__label">客戶（可留空）</span>
          <input
            className="np-field__input"
            type="text"
            value={client}
            onChange={(e) => setClient(e.target.value)}
            placeholder="例：晴晴"
            maxLength={255}
            autoComplete="off"
          />
        </label>

        <label className="np-field">
          <span className="np-field__label">影片風格</span>
          <select
            className="np-field__input"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            required
          >
            {PROFILE_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <fieldset className="np-ratios">
          <legend className="np-ratios__legend">IG / FB 成品比例</legend>
          <div className="np-ratios__grid">
            {RATIO_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className={`ratio-card${ratio === opt.value ? " ratio-card--on" : ""}`}
              >
                <input
                  className="ratio-card__radio"
                  type="radio"
                  name="ratio"
                  value={opt.value}
                  checked={ratio === opt.value}
                  onChange={() => setRatio(opt.value)}
                />
                <span
                  className="ratio-card__frame"
                  style={{ aspectRatio: opt.ratio }}
                  aria-hidden
                />
                <span className="ratio-card__label">{opt.label}</span>
                <span className="ratio-card__hint">{opt.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>

        {error && (
          <div className="np-error" role="alert">
            <span className="mono">建立失敗 · {error}</span>
          </div>
        )}

        <div className="np-actions">
          <button
            type="submit"
            className="np-submit"
            disabled={!canSubmit}
          >
            {submitting ? "建立中…" : "建立專案 →"}
          </button>
        </div>
      </form>
    </main>
  );
}
