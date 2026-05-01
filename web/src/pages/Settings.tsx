import { useEffect, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { SettingsOut } from "../api/types";
import "./Settings.css";

const DEFAULT_KEY_MANAGER_URL = "http://key.sisihome.org:7823";

const SOURCE_LABEL: Record<SettingsOut["llm_api_keys"]["source"], string> = {
  db: "資料庫（從這裡管理）",
  env: "環境變數 .env（fallback）",
  none: "未設定",
};

interface FlashMessage {
  kind: "ok" | "error";
  text: string;
}

export default function Settings() {
  const [data, setData] = useState<SettingsOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [textarea, setTextarea] = useState("");
  const [replace, setReplace] = useState(true);
  const [managerUrl, setManagerUrl] = useState(DEFAULT_KEY_MANAGER_URL);
  const [busy, setBusy] = useState<null | "save" | "sync" | "clear">(null);
  const [flash, setFlash] = useState<FlashMessage | null>(null);

  const refresh = async () => {
    try {
      const next = await apiClient.fetchSettings();
      setData(next);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const handleSave = async () => {
    setBusy("save");
    setFlash(null);
    try {
      const out = await apiClient.updateLLMKeys({
        raw: textarea,
        replace,
      });
      setFlash({
        kind: "ok",
        text: `已儲存 · 接受 ${out.accepted_count} / 拒絕 ${out.rejected_count} · 目前共 ${out.stored_count} 把 key`,
      });
      setTextarea("");
      await refresh();
    } catch (err) {
      setFlash({
        kind: "error",
        text:
          err instanceof ApiError
            ? `儲存失敗 (HTTP ${err.status}): ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
      });
    } finally {
      setBusy(null);
    }
  };

  const handleSync = async () => {
    setBusy("sync");
    setFlash(null);
    try {
      const out = await apiClient.syncKeysFromManager({
        url: managerUrl,
        trusted_only: true,
        replace: false,
      });
      setFlash({
        kind: "ok",
        text: `從 key-manager 抓取 ${out.fetched} 把 · 新匯入 ${out.imported} · 略過重複 ${out.skipped} · 目前共 ${out.stored_count} 把 key`,
      });
      await refresh();
    } catch (err) {
      setFlash({
        kind: "error",
        text:
          err instanceof ApiError
            ? `同步失敗 (HTTP ${err.status}): ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
      });
    } finally {
      setBusy(null);
    }
  };

  const handleClear = async () => {
    if (!confirm("確定要清空 DB 中的 key pool？清空後系統會 fallback 回環境變數的設定。")) {
      return;
    }
    setBusy("clear");
    setFlash(null);
    try {
      await apiClient.clearLLMKeys();
      setFlash({ kind: "ok", text: "已清空 DB key pool。" });
      await refresh();
    } catch (err) {
      setFlash({
        kind: "error",
        text:
          err instanceof ApiError
            ? `清空失敗 (HTTP ${err.status}): ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="settings page">
      <section className="settings__hero">
        <div className="settings__kicker">系統設定</div>
        <h1 className="settings__title">
          AI 模型與<em>金鑰池</em>
        </h1>
        <p className="settings__lede">
          場景分析、對稿、與草稿修補都共用同一組 Gemini key pool。
          從這裡批次匯入或從 key-manager 同步即可，無需重啟容器。
        </p>
      </section>

      {loadError && (
        <div className="settings__notice settings__notice--error" role="alert">
          無法載入設定 · {loadError}
        </div>
      )}

      {data && (
        <section className="settings__panel">
          <div className="settings__panel-head">
            <h2>目前狀態</h2>
          </div>
          <dl className="settings__kv">
            <dt>模型</dt>
            <dd className="mono">{data.llm_model}</dd>
            <dt>逾時</dt>
            <dd className="mono">{data.llm_timeout_s}s</dd>
            <dt>Key 數量</dt>
            <dd className="mono">{data.llm_api_keys.count}</dd>
            <dt>Key 來源</dt>
            <dd>{SOURCE_LABEL[data.llm_api_keys.source]}</dd>
            <dt>後 4 碼</dt>
            <dd className="mono settings__suffixes">
              {data.llm_api_keys.masked_suffixes.length === 0
                ? "—"
                : data.llm_api_keys.masked_suffixes.map((s) => (
                    <span key={s} className="settings__suffix-pill">
                      ···{s}
                    </span>
                  ))}
            </dd>
          </dl>
        </section>
      )}

      <section className="settings__panel">
        <div className="settings__panel-head">
          <h2>批次匯入 Gemini API Key</h2>
          <p className="settings__hint">
            支援逗號或換行分隔；可貼整段{" "}
            <code>LLM_API_KEYS=AIza...,AIza...</code> 行，會自動清理。
          </p>
        </div>
        <textarea
          className="settings__textarea mono"
          rows={8}
          placeholder={"AIzaSy...,AIzaSy...\n# 或一行一把 key\nAIzaSy..."}
          value={textarea}
          onChange={(e) => setTextarea(e.target.value)}
          spellCheck={false}
        />
        <div className="settings__row">
          <label className="settings__check">
            <input
              type="checkbox"
              checked={replace}
              onChange={(e) => setReplace(e.target.checked)}
            />
            <span>取代既有 pool（取消勾選＝合併）</span>
          </label>
          <div className="settings__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={handleSave}
              disabled={busy !== null || textarea.trim().length === 0}
            >
              {busy === "save" ? "儲存中…" : "儲存"}
            </button>
            <button
              type="button"
              className="cta cta--quiet"
              onClick={handleClear}
              disabled={busy !== null || data?.llm_api_keys.source !== "db"}
            >
              {busy === "clear" ? "清空中…" : "清空 DB pool"}
            </button>
          </div>
        </div>
      </section>

      <section className="settings__panel">
        <div className="settings__panel-head">
          <h2>從 key-manager 同步</h2>
          <p className="settings__hint">
            呼叫 <code>GET /api/keys/export?trusted_only=1</code> 抓出 trusted
            pool，與目前 DB 既有 key 合併（會去重）。
          </p>
        </div>
        <div className="settings__row settings__row--top">
          <label className="settings__field">
            <span>key-manager URL</span>
            <input
              type="url"
              className="settings__input mono"
              value={managerUrl}
              onChange={(e) => setManagerUrl(e.target.value)}
              spellCheck={false}
            />
          </label>
          <div className="settings__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={handleSync}
              disabled={busy !== null || managerUrl.trim().length === 0}
            >
              {busy === "sync" ? "同步中…" : "同步"}
            </button>
          </div>
        </div>
      </section>

      {flash && (
        <div
          className={`settings__notice settings__notice--${flash.kind}`}
          role={flash.kind === "error" ? "alert" : "status"}
        >
          {flash.text}
        </div>
      )}
    </main>
  );
}
