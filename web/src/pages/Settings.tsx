import { useEffect, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  OpenCodeModelOut,
  OpenCodeStatusOut,
  SettingsOut,
  StoryTtsStatusOut,
} from "../api/types";
import "./Settings.css";

const DEFAULT_KEY_MANAGER_URL = "http://key.sisihome.org:7823";

const SOURCE_LABEL: Record<SettingsOut["llm_api_keys"]["source"], string> = {
  db: "已在系統內管理",
  env: "使用主機環境設定（備援）",
  none: "未設定",
};

interface FlashMessage {
  kind: "ok" | "error";
  text: string;
}

const OC_VARIANT_LABELS: Record<string, string> = {
  default: "預設",
  medium: "中等",
  high: "高品質",
};

const OC_SOURCE_LABEL: Record<string, string> = {
  setting: "DB 設定",
  env: "環境變數",
  default: "預設值",
  none: "未設定",
};

export default function Settings() {
  const [data, setData] = useState<SettingsOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [textarea, setTextarea] = useState("");
  const [replace, setReplace] = useState(true);
  const [managerUrl, setManagerUrl] = useState(DEFAULT_KEY_MANAGER_URL);
  const [busy, setBusy] = useState<null | "save" | "sync" | "clear">(null);
  const [flash, setFlash] = useState<FlashMessage | null>(null);

  // OpenCode settings state
  const [ocStatus, setOcStatus] = useState<OpenCodeStatusOut | null>(null);
  const [ocModels, setOcModels] = useState<OpenCodeModelOut[]>([]);
  const [ocModelsLoading, setOcModelsLoading] = useState(false);
  const [ocModelSearch, setOcModelSearch] = useState("");
  const [ocServersInput, setOcServersInput] = useState("");
  const [ocTextModel, setOcTextModel] = useState("");
  const [ocTextVariant, setOcTextVariant] = useState("");
  const [ocSaving, setOcSaving] = useState(false);
  const [ocFlash, setOcFlash] = useState<FlashMessage | null>(null);
  const [ttsStatus, setTtsStatus] = useState<StoryTtsStatusOut | null>(null);
  const [ttsProvider, setTtsProvider] = useState("");
  const [ttsVoice, setTtsVoice] = useState("");
  const [ttsModel, setTtsModel] = useState("");
  const [ttsTimeout, setTtsTimeout] = useState("45");
  const [ttsSaving, setTtsSaving] = useState(false);
  const [ttsFlash, setTtsFlash] = useState<FlashMessage | null>(null);

  const refresh = async () => {
    try {
      const next = await apiClient.fetchSettings();
      setData(next);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  };

  const loadOpenCode = async () => {
    try {
      const status = await apiClient.getOpenCodeStatus();
      setOcStatus(status);
      setOcServersInput(status.servers.map((s) => s.base_url).join("\n"));
      setOcTextModel(status.text_model_source === "setting" ? status.text_model : "");
      setOcTextVariant(status.text_variant_source === "setting" ? status.text_variant : "");
    } catch {
      // non-fatal
    }
  };

  const applyStoryTtsStatus = (status: StoryTtsStatusOut) => {
    setTtsStatus(status);
    setTtsProvider(status.provider_source === "setting" ? status.provider : "");
    setTtsVoice(status.voice_source === "setting" ? status.voice : "");
    setTtsModel(status.model_source === "setting" ? status.model : "");
    setTtsTimeout(status.timeout_source === "setting" ? String(status.timeout_s) : "");
  };

  const loadStoryTts = async () => {
    try {
      applyStoryTtsStatus(await apiClient.getStoryTtsStatus());
    } catch {
      // non-fatal
    }
  };

  const loadOpenCodeModels = async () => {
    setOcModelsLoading(true);
    try {
      const result = await apiClient.getOpenCodeModels();
      setOcModels(result.models);
      if (result.warning) {
        setOcFlash({ kind: "error", text: result.warning });
      }
    } catch (err) {
      setOcFlash({
        kind: "error",
        text: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setOcModelsLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    void loadOpenCode();
    void loadStoryTts();
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
        text: `已儲存 · 接受 ${out.accepted_count} / 拒絕 ${out.rejected_count} · 目前共 ${out.stored_count} 把金鑰`,
      });
      setTextarea("");
      await refresh();
    } catch (err) {
      setFlash({
        kind: "error",
        text:
          err instanceof ApiError
            ? `儲存失敗：${err.message}`
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
        text: `從金鑰管理服務抓取 ${out.fetched} 把 · 新匯入 ${out.imported} · 略過重複 ${out.skipped} · 目前共 ${out.stored_count} 把金鑰`,
      });
      await refresh();
    } catch (err) {
      setFlash({
        kind: "error",
        text:
          err instanceof ApiError
            ? `同步失敗：${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
      });
    } finally {
      setBusy(null);
    }
  };

  const handleClear = async () => {
    if (!confirm("確定要清空系統內金鑰？清空後會改用主機環境設定。")) {
      return;
    }
    setBusy("clear");
    setFlash(null);
    try {
      await apiClient.clearLLMKeys();
      setFlash({ kind: "ok", text: "已清空系統內金鑰。" });
      await refresh();
    } catch (err) {
      setFlash({
        kind: "error",
        text:
          err instanceof ApiError
            ? `清空失敗：${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
      });
    } finally {
      setBusy(null);
    }
  };

  const handleSaveOpenCode = async () => {
    setOcSaving(true);
    setOcFlash(null);
    try {
      const updated = await apiClient.saveOpenCodeSettings({
        servers: ocServersInput,
        text_model: ocTextModel,
        text_variant: ocTextVariant,
      });
      setOcStatus(updated);
      setOcFlash({ kind: "ok", text: "OpenCode 設定已儲存。" });
      await loadOpenCodeModels();
    } catch (err) {
      setOcFlash({
        kind: "error",
        text: err instanceof ApiError ? `儲存失敗：${err.message}` : String(err),
      });
    } finally {
      setOcSaving(false);
    }
  };

  const handleClearOpenCode = async () => {
    if (!confirm("確定要清除 DB 中的 OpenCode 設定？清除後將改讀環境變數。")) return;
    setOcSaving(true);
    setOcFlash(null);
    try {
      const updated = await apiClient.clearOpenCodeSettings();
      setOcStatus(updated);
      setOcServersInput(updated.servers.map((s) => s.base_url).join("\n"));
      setOcTextModel(updated.text_model_source === "setting" ? updated.text_model : "");
      setOcTextVariant(updated.text_variant_source === "setting" ? updated.text_variant : "");
      setOcFlash({ kind: "ok", text: "OpenCode DB 設定已清除。" });
    } catch (err) {
      setOcFlash({
        kind: "error",
        text: err instanceof ApiError ? `清除失敗：${err.message}` : String(err),
      });
    } finally {
      setOcSaving(false);
    }
  };

  const handleSaveStoryTts = async () => {
    setTtsSaving(true);
    setTtsFlash(null);
    try {
      const updated = await apiClient.saveStoryTtsSettings({
        provider: ttsProvider,
        voice: ttsVoice,
        model: ttsModel,
        timeout_s: ttsTimeout.trim() ? Number(ttsTimeout) : undefined,
      });
      applyStoryTtsStatus(updated);
      setTtsFlash({ kind: "ok", text: "Story/Narrato TTS 設定已儲存。" });
    } catch (err) {
      setTtsFlash({
        kind: "error",
        text: err instanceof ApiError ? `儲存失敗：${err.message}` : String(err),
      });
    } finally {
      setTtsSaving(false);
    }
  };

  const handleClearStoryTts = async () => {
    if (!confirm("確定要清除 DB 中的 Story/Narrato TTS 設定？清除後將改讀環境變數。")) return;
    setTtsSaving(true);
    setTtsFlash(null);
    try {
      applyStoryTtsStatus(await apiClient.clearStoryTtsSettings());
      setTtsFlash({ kind: "ok", text: "Story/Narrato TTS DB 設定已清除。" });
    } catch (err) {
      setTtsFlash({
        kind: "error",
        text: err instanceof ApiError ? `清除失敗：${err.message}` : String(err),
      });
    } finally {
      setTtsSaving(false);
    }
  };

  const filteredModels = ocModelSearch
    ? ocModels.filter(
        (m) =>
          m.id.toLowerCase().includes(ocModelSearch.toLowerCase()) ||
          m.name.toLowerCase().includes(ocModelSearch.toLowerCase()),
      )
    : ocModels;

  const modelsByProvider = filteredModels.reduce<Record<string, OpenCodeModelOut[]>>(
    (acc, m) => {
      (acc[m.provider] ??= []).push(m);
      return acc;
    },
    {},
  );

  return (
    <main className="settings page">
      <section className="settings__hero">
        <div className="settings__kicker">系統設定</div>
        <h1 className="settings__title">
          AI 服務與<em>金鑰管理</em>
        </h1>
        <p className="settings__lede">
          素材檢查、腳本對照與 AI 建議都共用這組金鑰。可批次匯入或從金鑰管理服務同步，無需重啟服務。
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
            <dt>金鑰數量</dt>
            <dd className="mono">{data.llm_api_keys.count}</dd>
            <dt>金鑰來源</dt>
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
          <h2>批次匯入 AI 服務金鑰</h2>
          <p className="settings__hint">
            支援逗號或換行分隔；可貼整段{" "}
            <code>LLM_API_KEYS=AIza...,AIza...</code> 行，會自動清理。
          </p>
        </div>
        <textarea
          className="settings__textarea mono"
          rows={8}
          placeholder={"AIzaSy...,AIzaSy...\n# 或一行一把金鑰\nAIzaSy..."}
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
            <span>取代既有金鑰（取消勾選＝合併）</span>
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
              {busy === "clear" ? "清空中…" : "清空系統內金鑰"}
            </button>
          </div>
        </div>
      </section>

      <section className="settings__panel">
        <div className="settings__panel-head">
          <h2>從金鑰管理服務同步</h2>
          <p className="settings__hint">
            從金鑰管理服務抓取可用金鑰，與目前系統內既有金鑰合併（會去重）。
            進階：使用 trusted-only 匯出。
          </p>
        </div>
        <div className="settings__row settings__row--top">
          <label className="settings__field">
            <span>金鑰管理服務 URL</span>
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

      <section className="settings__panel">
        <div className="settings__panel-head">
          <h2>OpenCode AI 服務設定</h2>
          <p className="settings__hint">
            文字生成與 NarratoAI 視覺幀分析都優先走 OpenCode provider；Gemini 金鑰只作 legacy fallback。未填寫時會讀取主機環境設定。
          </p>
        </div>

        {ocStatus && (
          <dl className="settings__kv">
            <dt>伺服器</dt>
            <dd className="mono">
              {ocStatus.servers.length === 0
                ? "—"
                : ocStatus.servers.map((s) => s.base_url).join(", ")}{" "}
              <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: "var(--t-xs)" }}>
                [{OC_SOURCE_LABEL[ocStatus.servers_source] ?? ocStatus.servers_source}]
              </span>
            </dd>
            <dt>文字模型</dt>
            <dd className="mono">
              {ocStatus.text_model}{" "}
              <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: "var(--t-xs)" }}>
                [{OC_SOURCE_LABEL[ocStatus.text_model_source] ?? ocStatus.text_model_source}]
              </span>
            </dd>
            <dt>品質</dt>
            <dd className="mono">
              {OC_VARIANT_LABELS[ocStatus.text_variant] ?? ocStatus.text_variant}{" "}
              <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: "var(--t-xs)" }}>
                [{OC_SOURCE_LABEL[ocStatus.text_variant_source] ?? ocStatus.text_variant_source}]
              </span>
            </dd>
          </dl>
        )}

        <div className="settings__row settings__row--top" style={{ marginTop: "var(--space-5)" }}>
          <label className="settings__field" style={{ flex: "1 1 100%" }}>
            <span>OpenCode 伺服器（一行一個 URL）</span>
            <textarea
              className="settings__textarea mono"
              rows={3}
              placeholder="https://provider-amd.sisihome.org"
              value={ocServersInput}
              onChange={(e) => setOcServersInput(e.target.value)}
              spellCheck={false}
            />
          </label>
        </div>

        <div className="settings__row settings__row--top">
          <label className="settings__field">
            <span>搜尋模型</span>
            <input
              type="search"
              className="settings__input"
              placeholder="gpt / gemini / …"
              value={ocModelSearch}
              onChange={(e) => setOcModelSearch(e.target.value)}
            />
          </label>
          <div className="settings__actions" style={{ alignSelf: "flex-end" }}>
            <button
              type="button"
              className="cta cta--quiet"
              onClick={() => void loadOpenCodeModels()}
              disabled={ocModelsLoading}
            >
              {ocModelsLoading ? "載入中…" : "重新整理模型"}
            </button>
          </div>
        </div>

        <div className="settings__row settings__row--top">
          <label className="settings__field">
            <span>文字模型</span>
            <select
              className="settings__input"
              value={ocTextModel}
              onChange={(e) => setOcTextModel(e.target.value)}
            >
              <option value="">
                — 使用預設（{ocStatus ? ocStatus.text_model : "openai/gpt-5.5"}）—
              </option>
              {Object.entries(modelsByProvider)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([provider, models]) => (
                  <optgroup key={provider} label={provider}>
                    {[...models]
                      .sort((a, b) => a.id.localeCompare(b.id))
                      .map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name || m.id}
                        </option>
                      ))}
                  </optgroup>
                ))}
            </select>
          </label>
          <label className="settings__field">
            <span>品質等級</span>
            <select
              className="settings__input"
              value={ocTextVariant}
              onChange={(e) => setOcTextVariant(e.target.value)}
            >
              <option value="">
                — 使用預設（
                {ocStatus
                  ? (OC_VARIANT_LABELS[ocStatus.text_variant] ?? ocStatus.text_variant)
                  : "中等"}
                ）—
              </option>
              {Object.entries(OC_VARIANT_LABELS).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="settings__row">
          <div className="settings__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleSaveOpenCode()}
              disabled={ocSaving}
            >
              {ocSaving ? "儲存中…" : "儲存 OpenCode 設定"}
            </button>
            <button
              type="button"
              className="cta cta--quiet"
              onClick={() => void handleClearOpenCode()}
              disabled={ocSaving}
            >
              清除 DB 設定
            </button>
          </div>
        </div>

        {ocFlash && (
          <div
            className={`settings__notice settings__notice--${ocFlash.kind}`}
            role={ocFlash.kind === "error" ? "alert" : "status"}
          >
            {ocFlash.text}
          </div>
        )}
      </section>

      <section className="settings__panel">
        <div className="settings__panel-head">
          <h2>Story/Narrato TTS 設定</h2>
          <p className="settings__hint">
            Story、紀錄片解說與短劇解說共用這組旁白設定。provider 留空時不產生 TTS，仍會保留字幕版 fallback。
          </p>
        </div>

        {ttsStatus && (
          <dl className="settings__kv">
            <dt>Provider</dt>
            <dd className="mono">
              {ttsStatus.provider || "未啟用"}{" "}
              <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: "var(--t-xs)" }}>
                [{OC_SOURCE_LABEL[ttsStatus.provider_source] ?? ttsStatus.provider_source}]
              </span>
            </dd>
            <dt>Voice</dt>
            <dd className="mono">
              {ttsStatus.voice}{" "}
              <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: "var(--t-xs)" }}>
                [{OC_SOURCE_LABEL[ttsStatus.voice_source] ?? ttsStatus.voice_source}]
              </span>
            </dd>
            <dt>Model</dt>
            <dd className="mono">
              {ttsStatus.model}{" "}
              <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: "var(--t-xs)" }}>
                [{OC_SOURCE_LABEL[ttsStatus.model_source] ?? ttsStatus.model_source}]
              </span>
            </dd>
            <dt>Timeout</dt>
            <dd className="mono">
              {ttsStatus.timeout_s}s{" "}
              <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: "var(--t-xs)" }}>
                [{OC_SOURCE_LABEL[ttsStatus.timeout_source] ?? ttsStatus.timeout_source}]
              </span>
            </dd>
          </dl>
        )}

        <div className="settings__row settings__row--top" style={{ marginTop: "var(--space-5)" }}>
          <label className="settings__field">
            <span>TTS Provider</span>
            <select
              className="settings__input"
              value={ttsProvider}
              onChange={(e) => setTtsProvider(e.target.value)}
            >
              <option value="">— 使用預設 / 未啟用 —</option>
              <option value="edge">edge</option>
              <option value="azure">azure</option>
              <option value="tencent">tencent</option>
              <option value="silent">silent（測試）</option>
            </select>
          </label>
          <label className="settings__field">
            <span>Voice</span>
            <input
              className="settings__input mono"
              placeholder={ttsStatus?.voice || "zh-TW-HsiaoChenNeural"}
              value={ttsVoice}
              onChange={(e) => setTtsVoice(e.target.value)}
              spellCheck={false}
            />
          </label>
        </div>

        <div className="settings__row settings__row--top">
          <label className="settings__field">
            <span>Model / Provider 標籤</span>
            <input
              className="settings__input mono"
              placeholder={ttsStatus?.model || "edge-tts"}
              value={ttsModel}
              onChange={(e) => setTtsModel(e.target.value)}
              spellCheck={false}
            />
          </label>
          <label className="settings__field">
            <span>Timeout 秒數</span>
            <input
              type="number"
              min={1}
              max={300}
              className="settings__input mono"
              placeholder={ttsStatus ? String(ttsStatus.timeout_s) : "45"}
              value={ttsTimeout}
              onChange={(e) => setTtsTimeout(e.target.value)}
            />
          </label>
        </div>

        <div className="settings__row">
          <div className="settings__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleSaveStoryTts()}
              disabled={ttsSaving}
            >
              {ttsSaving ? "儲存中…" : "儲存 TTS 設定"}
            </button>
            <button
              type="button"
              className="cta cta--quiet"
              onClick={() => void handleClearStoryTts()}
              disabled={ttsSaving}
            >
              清除 DB 設定
            </button>
          </div>
        </div>

        {ttsFlash && (
          <div
            className={`settings__notice settings__notice--${ttsFlash.kind}`}
            role={ttsFlash.kind === "error" ? "alert" : "status"}
          >
            {ttsFlash.text}
          </div>
        )}
      </section>
    </main>
  );
}
