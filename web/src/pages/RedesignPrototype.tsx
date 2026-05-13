import {
  DndContext,
  PointerSensor,
  closestCenter,
  type DragEndEvent,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useMemo, useState } from "react";
import "./RedesignPrototype.css";

type Screen = "list" | "upload" | "analysis" | "edit" | "timeline";
type Mode = "idle" | "auto" | "manual";
type AssetVariant = "raw" | "stabilized";
type TrackingMode = "none" | "subject" | "manual";

type AssetDecision = {
  id: string;
  name: string;
  duration: string;
  notes: string;
  stabilization: "done" | "running" | "pending";
  variant: AssetVariant;
  tracking: TrackingMode;
};

type SegmentPlan = {
  id: string;
  label: string;
  assetId: string;
  assetName: string;
  reason: string;
  start: number;
  end: number;
  energy: "high" | "mid" | "low";
};

const screens: Array<{ key: Screen; label: string }> = [
  { key: "list", label: "ProjectList" },
  { key: "upload", label: "Upload" },
  { key: "analysis", label: "Analysis" },
  { key: "edit", label: "Edit" },
  { key: "timeline", label: "Timeline" },
];

const autoStages = [
  "分析素材",
  "等待防抖完成",
  "挑選素材版本",
  "生成 AI 初稿",
  "配樂與輸出設定",
  "Render 第一版",
];

const seedAssets: AssetDecision[] = [
  {
    id: "a1",
    name: "DJI 河堤追焦",
    duration: "00:14",
    notes: "主角穩定，適合當開場主鏡頭",
    stabilization: "done",
    variant: "stabilized",
    tracking: "subject",
  },
  {
    id: "a2",
    name: "iPhone 側拍轉身",
    duration: "00:09",
    notes: "人臉近，切 reaction 很好用",
    stabilization: "running",
    variant: "raw",
    tracking: "manual",
  },
  {
    id: "a3",
    name: "DJI 廣角街景",
    duration: "00:18",
    notes: "空景可補節奏，但不需要 tracking",
    stabilization: "pending",
    variant: "raw",
    tracking: "none",
  },
  {
    id: "a4",
    name: "Sony 特寫手部",
    duration: "00:11",
    notes: "細節鏡頭，適合放 beat drop 前",
    stabilization: "done",
    variant: "stabilized",
    tracking: "manual",
  },
];

const seedSegments: SegmentPlan[] = [
  {
    id: "s1",
    label: "Hook 開場",
    assetId: "a1",
    assetName: "DJI 河堤追焦",
    reason: "前 2 秒速度感最強，適合當停留鉤子",
    start: 1.2,
    end: 4.8,
    energy: "high",
  },
  {
    id: "s2",
    label: "人物反應",
    assetId: "a2",
    assetName: "iPhone 側拍轉身",
    reason: "轉頭瞬間有表情，能接旁白句點",
    start: 0.8,
    end: 3.2,
    energy: "mid",
  },
  {
    id: "s3",
    label: "節奏補景",
    assetId: "a3",
    assetName: "DJI 廣角街景",
    reason: "街景拉空，讓後面細節鏡頭更有層次",
    start: 5.1,
    end: 7.4,
    energy: "low",
  },
  {
    id: "s4",
    label: "細節收尾",
    assetId: "a4",
    assetName: "Sony 特寫手部",
    reason: "手部動作乾淨，適合接 logo 或 CTA",
    start: 2.4,
    end: 5.6,
    energy: "mid",
  },
];

function formatSeconds(value: number) {
  return `${value.toFixed(1)}s`;
}

function prototypeStatus(mode: Mode, autoStageIndex: number) {
  if (mode === "auto") {
    if (autoStageIndex >= autoStages.length) return "全自動已完成第一版";
    return `全自動執行中: ${autoStages[autoStageIndex]}`;
  }
  if (mode === "manual") return "手動流程: 先確認素材與片段，再進輸出設定";
  return "先選你要一鍵做完，還是自己掌控素材與片段";
}

function SortableSegmentCard({
  segment,
  assets,
  onUpdate,
  onRemove,
  selected,
  onSelect,
}: {
  segment: SegmentPlan;
  assets: AssetDecision[];
  onUpdate: (segmentId: string, patch: Partial<SegmentPlan>) => void;
  onRemove: (segmentId: string) => void;
  selected: boolean;
  onSelect: (segmentId: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: segment.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <article
      ref={setNodeRef}
      style={style}
      className={`prototype-segment-card${selected ? " is-selected" : ""}${
        isDragging ? " is-dragging" : ""
      }`}
      onClick={() => onSelect(segment.id)}
    >
      <div className="prototype-segment-card__topline">
        <button
          type="button"
          className="prototype-drag-handle"
          aria-label={`拖曳排序 ${segment.label}`}
          {...attributes}
          {...listeners}
        >
          ≡
        </button>
        <div>
          <p className="prototype-segment-card__eyebrow">AI 初稿片段</p>
          <h4>{segment.label}</h4>
        </div>
        <span className={`prototype-energy-badge energy-${segment.energy}`}>{segment.energy}</span>
      </div>

      <p className="prototype-segment-card__reason">{segment.reason}</p>

      <div className="prototype-segment-grid">
        <label>
          素材來源
          <select
            value={segment.assetId}
            onChange={(event) => {
              const nextAsset = assets.find((asset) => asset.id === event.target.value);
              if (!nextAsset) return;
              onUpdate(segment.id, {
                assetId: nextAsset.id,
                assetName: nextAsset.name,
              });
            }}
          >
            {assets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.name}
              </option>
            ))}
          </select>
        </label>

        <label>
          In
          <input
            type="number"
            min={0}
            max={Math.max(segment.end - 0.1, 0.1)}
            step={0.1}
            value={segment.start}
            onChange={(event) => {
              const nextStart = Number(event.target.value);
              onUpdate(segment.id, {
                start: Math.min(nextStart, segment.end - 0.1),
              });
            }}
          />
        </label>

        <label>
          Out
          <input
            type="number"
            min={segment.start + 0.1}
            max={20}
            step={0.1}
            value={segment.end}
            onChange={(event) => {
              const nextEnd = Number(event.target.value);
              onUpdate(segment.id, {
                end: Math.max(nextEnd, segment.start + 0.1),
              });
            }}
          />
        </label>
      </div>

      <div className="prototype-segment-card__footer">
        <span>{segment.assetName}</span>
        <span>
          長度 {formatSeconds(Math.max(segment.end - segment.start, 0.1))}
        </span>
        <button
          type="button"
          className="prototype-text-button prototype-text-button--danger"
          onClick={(event) => {
            event.stopPropagation();
            onRemove(segment.id);
          }}
        >
          刪除片段
        </button>
      </div>
    </article>
  );
}

export default function RedesignPrototype() {
  const [screen, setScreen] = useState<Screen>("list");
  const [mode, setMode] = useState<Mode>("idle");
  const [autoStageIndex, setAutoStageIndex] = useState(0);
  const [assets, setAssets] = useState(seedAssets);
  const [segments, setSegments] = useState(seedSegments);
  const [selectedSegmentId, setSelectedSegmentId] = useState(seedSegments[0]?.id ?? "");
  const [musicSource, setMusicSource] = useState("ai");
  const [musicTone, setMusicTone] = useState("冷冽節奏");
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [subtitleStyle, setSubtitleStyle] = useState("極簡白字");
  const [previewReady, setPreviewReady] = useState(false);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));

  const selectedSegment =
    segments.find((segment) => segment.id === selectedSegmentId) ?? segments[0] ?? null;

  const stabilizedCount = assets.filter((asset) => asset.stabilization === "done").length;
  const analysisSummary = `${assets.length} 支素材 / ${stabilizedCount} 支已有防抖版 / ${segments.length} 段 AI 初稿`;

  const pipelineLabel = useMemo(
    () => prototypeStatus(mode, autoStageIndex),
    [mode, autoStageIndex],
  );

  function updateAsset(assetId: string, patch: Partial<AssetDecision>) {
    setAssets((current) =>
      current.map((asset) => (asset.id === assetId ? { ...asset, ...patch } : asset)),
    );
  }

  function updateSegment(segmentId: string, patch: Partial<SegmentPlan>) {
    setSegments((current) =>
      current.map((segment) =>
        segment.id === segmentId ? { ...segment, ...patch } : segment,
      ),
    );
  }

  function removeSegment(segmentId: string) {
    setSegments((current) => {
      const next = current.filter((segment) => segment.id !== segmentId);
      if (selectedSegmentId === segmentId) {
        setSelectedSegmentId(next[0]?.id ?? "");
      }
      return next;
    });
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setSegments((current) => {
      const oldIndex = current.findIndex((segment) => segment.id === active.id);
      const newIndex = current.findIndex((segment) => segment.id === over.id);
      return arrayMove(current, oldIndex, newIndex);
    });
  }

  function startAutoFlow(nextScreen: Screen) {
    setMode("auto");
    setScreen(nextScreen);
    setAutoStageIndex(0);
    setPreviewReady(false);
  }

  function advanceAutoFlow() {
    setAutoStageIndex((current) => {
      const next = Math.min(current + 1, autoStages.length);
      if (next >= autoStages.length) {
        setPreviewReady(true);
        setScreen("edit");
      }
      return next;
    });
  }

  function markAllStabilized() {
    setAssets((current) =>
      current.map((asset) => ({
        ...asset,
        stabilization: "done",
        variant: asset.variant === "raw" ? "stabilized" : asset.variant,
      })),
    );
  }

  return (
    <main className="prototype-page">
      <section className="prototype-hero">
        <div>
          <p className="prototype-kicker">Interactive Prototype</p>
          <h1>新版短影音流程互動稿</h1>
          <p className="prototype-lede">
            這不是說明文件。這頁就是讓你直接點流程、改片段、切換自動與手動路徑，看畫面應該怎麼長。
          </p>
        </div>
        <div className="prototype-status-card">
          <span className={`prototype-mode-pill mode-${mode}`}>{mode === "idle" ? "未選模式" : mode === "auto" ? "全自動" : "手動掌控"}</span>
          <h2>{pipelineLabel}</h2>
          <p>目前畫面: {screens.find((item) => item.key === screen)?.label}</p>
          {mode === "auto" ? (
            <button type="button" className="prototype-primary-button" onClick={advanceAutoFlow}>
              模擬跑下一步
            </button>
          ) : null}
        </div>
      </section>

      <section className="prototype-nav-strip" aria-label="原型畫面切換">
        {screens.map((item, index) => (
          <button
            key={item.key}
            type="button"
            className={`prototype-step-chip${screen === item.key ? " is-active" : ""}`}
            onClick={() => setScreen(item.key)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{item.label}</strong>
          </button>
        ))}
      </section>

      <section className="prototype-layout">
        <aside className="prototype-sidebar">
          <div className="prototype-panel">
            <p className="prototype-panel__eyebrow">你要看的是</p>
            <ul className="prototype-checklist">
              <li>ProjectList 怎麼直接給兩條路</li>
              <li>Upload 怎麼不再把人卡死在 setup</li>
              <li>Analysis 怎麼先做素材決策與片段確認</li>
              <li>Edit 怎麼只留音樂與輸出設定</li>
              <li>Timeline 怎麼退到進階細修</li>
            </ul>
          </div>

          <div className="prototype-panel">
            <p className="prototype-panel__eyebrow">全自動排程</p>
            <ol className="prototype-pipeline-list">
              {autoStages.map((label, index) => {
                const state =
                  index < autoStageIndex
                    ? "done"
                    : index === autoStageIndex && mode === "auto"
                      ? "running"
                      : "todo";
                return (
                  <li key={label} className={`pipeline-${state}`}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{label}</strong>
                  </li>
                );
              })}
            </ol>
          </div>

          <div className="prototype-panel">
            <p className="prototype-panel__eyebrow">關鍵確認</p>
            <p className="prototype-note">
              目前這版 prototype 的核心就是: 手動模式先看 AI 怎麼剪，再決定配樂與 render；全自動則是一鍵排到底。
            </p>
          </div>
        </aside>

        <div className="prototype-canvas">
          {screen === "list" ? (
            <section className="prototype-screen">
              <header className="prototype-screen__header">
                <div>
                  <p className="prototype-screen__eyebrow">ProjectList</p>
                  <h2>每張專案卡直接顯示下一步</h2>
                </div>
                <button type="button" className="prototype-ghost-button" onClick={() => setScreen("upload")}>
                  看 Upload 畫面
                </button>
              </header>

              <div className="prototype-project-card prototype-project-card--featured">
                <div>
                  <p className="prototype-card__meta">#011 / 機車形象短片 / 12 支素材</p>
                  <h3>今天要剪什麼，不需要先猜下一步</h3>
                  <p className="prototype-card__body">
                    你可以直接一鍵做完，或先進去看 AI 怎麼挑素材與片段。狀態用人話寫，不再逼你讀工程名詞。
                  </p>
                  <div className="prototype-project-state-row">
                    <span className="prototype-state-badge state-ready">現在可以決定素材怎麼剪</span>
                    <span className="prototype-state-badge">已有 1 個可預覽版本</span>
                  </div>
                </div>

                <div className="prototype-project-actions">
                  <button
                    type="button"
                    className="prototype-primary-button prototype-primary-button--xl"
                    onClick={() => startAutoFlow("analysis")}
                  >
                    一鍵自動產生短影音
                  </button>
                  <button
                    type="button"
                    className="prototype-secondary-button"
                    onClick={() => {
                      setMode("manual");
                      setScreen("analysis");
                    }}
                  >
                    我要自己調素材與片段
                  </button>
                </div>
              </div>

              <div className="prototype-project-grid">
                <article className="prototype-project-card">
                  <p className="prototype-card__meta">#012 / 咖啡店開幕 Reels</p>
                  <h3>還沒上傳素材</h3>
                  <p className="prototype-card__body">卡片只講現在缺什麼，不讓你猜隱藏流程。</p>
                  <button
                    type="button"
                    className="prototype-ghost-button"
                    onClick={() => {
                      setMode("idle");
                      setScreen("upload");
                    }}
                  >
                    進入上傳
                  </button>
                </article>

                <article className="prototype-project-card">
                  <p className="prototype-card__meta">#013 / 房仲案場導覽</p>
                  <h3>可以先預覽成品</h3>
                  <p className="prototype-card__body">已經有第一版的人，不需要再被送回分析或設定頁。</p>
                  <button type="button" className="prototype-ghost-button" onClick={() => setScreen("edit")}>
                    直接看版本
                  </button>
                </article>
              </div>
            </section>
          ) : null}

          {screen === "upload" ? (
            <section className="prototype-screen">
              <header className="prototype-screen__header">
                <div>
                  <p className="prototype-screen__eyebrow">Upload</p>
                  <h2>素材準備完，當場就分流</h2>
                </div>
                <button type="button" className="prototype-ghost-button" onClick={() => setScreen("analysis")}>
                  跳到 Analysis
                </button>
              </header>

              <div className="prototype-upload-grid">
                <article className="prototype-panel prototype-upload-dropzone">
                  <p className="prototype-panel__eyebrow">素材上傳區</p>
                  <h3>12 支影片已完成上傳</h3>
                  <p className="prototype-note">拖拉區不需要花樣，重點是上傳後立刻告訴使用者下一步能怎麼走。</p>
                  <div className="prototype-upload-metrics">
                    <span>DJI 5</span>
                    <span>iPhone 4</span>
                    <span>Sony 3</span>
                  </div>
                </article>

                <article className="prototype-panel prototype-script-card">
                  <p className="prototype-panel__eyebrow">腳本 / 說明</p>
                  <textarea
                    value="開頭先給速度感，30 秒內做出都會感與人物細節。"
                    readOnly
                    aria-label="腳本預覽"
                  />
                </article>
              </div>

              <div className="prototype-split-actions">
                <button
                  type="button"
                  className="prototype-primary-button prototype-primary-button--split"
                  onClick={() => startAutoFlow("analysis")}
                >
                  直接一鍵自動幫我做完
                </button>
                <button
                  type="button"
                  className="prototype-secondary-button prototype-secondary-button--split"
                  onClick={() => {
                    setMode("manual");
                    setScreen("analysis");
                  }}
                >
                  我要先看 AI 怎麼挑片段
                </button>
              </div>
            </section>
          ) : null}

          {screen === "analysis" ? (
            <section className="prototype-screen">
              <header className="prototype-screen__header">
                <div>
                  <p className="prototype-screen__eyebrow">ProjectAnalysis</p>
                  <h2>先決定素材與片段，不先逼你 render</h2>
                  <p className="prototype-screen__subcopy">{analysisSummary}</p>
                </div>
                <div className="prototype-inline-actions">
                  <button type="button" className="prototype-ghost-button" onClick={markAllStabilized}>
                    一鍵產生防抖版
                  </button>
                  <button
                    type="button"
                    className="prototype-primary-button"
                    onClick={() => {
                      setMode("manual");
                      setScreen("edit");
                    }}
                  >
                    確認片段清單，進入輸出設定
                  </button>
                </div>
              </header>

              <div className="prototype-analysis-hero">
                <div>
                  <span className="prototype-state-badge state-ready">建議: 先等防抖完成，再決定 tracking 與版本</span>
                  <h3>這頁要做的是前置決策，不是看一堆狀態就卡住</h3>
                </div>
                <p>
                  使用者在這裡看得到 AI 初稿片段清單，也能改素材版本、tracking 方式、片段順序與 in/out，等確認後才去處理音樂與輸出。
                </p>
              </div>

              <div className="prototype-analysis-grid">
                <section className="prototype-panel">
                  <div className="prototype-section-title-row">
                    <div>
                      <p className="prototype-panel__eyebrow">素材決策</p>
                      <h3>先決定每支素材要用 raw 還是 stabilized</h3>
                    </div>
                  </div>
                  <div className="prototype-asset-list">
                    {assets.map((asset) => (
                      <article key={asset.id} className="prototype-asset-card">
                        <div className="prototype-asset-card__header">
                          <div>
                            <h4>{asset.name}</h4>
                            <p>{asset.duration} · {asset.notes}</p>
                          </div>
                          <span className={`prototype-state-badge state-${asset.stabilization}`}>
                            防抖 {asset.stabilization}
                          </span>
                        </div>

                        <div className="prototype-asset-card__controls">
                          <label>
                            素材版本
                            <select
                              value={asset.variant}
                              onChange={(event) =>
                                updateAsset(asset.id, {
                                  variant: event.target.value as AssetVariant,
                                })
                              }
                            >
                              <option value="raw">raw</option>
                              <option value="stabilized">stabilized</option>
                            </select>
                          </label>

                          <label>
                            Tracking
                            <select
                              value={asset.tracking}
                              onChange={(event) =>
                                updateAsset(asset.id, {
                                  tracking: event.target.value as TrackingMode,
                                })
                              }
                            >
                              <option value="none">none</option>
                              <option value="subject">主體追蹤</option>
                              <option value="manual">手動定點</option>
                            </select>
                          </label>
                        </div>
                      </article>
                    ))}
                  </div>
                </section>

                <section className="prototype-panel">
                  <div className="prototype-section-title-row">
                    <div>
                      <p className="prototype-panel__eyebrow">AI 初稿片段清單</p>
                      <h3>這裡就能排順序、修 in/out、換素材</h3>
                    </div>
                  </div>

                  <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                    <SortableContext items={segments.map((segment) => segment.id)} strategy={verticalListSortingStrategy}>
                      <div className="prototype-segment-list">
                        {segments.map((segment) => (
                          <SortableSegmentCard
                            key={segment.id}
                            segment={segment}
                            assets={assets}
                            onUpdate={updateSegment}
                            onRemove={removeSegment}
                            selected={segment.id === selectedSegmentId}
                            onSelect={setSelectedSegmentId}
                          />
                        ))}
                      </div>
                    </SortableContext>
                  </DndContext>
                </section>
              </div>
            </section>
          ) : null}

          {screen === "edit" ? (
            <section className="prototype-screen">
              <header className="prototype-screen__header">
                <div>
                  <p className="prototype-screen__eyebrow">ProjectEdit</p>
                  <h2>這頁只做配樂、輸出、版本預覽</h2>
                  <p className="prototype-screen__subcopy">不再把 AI 選片、tracking 與版本決策塞進這一頁。</p>
                </div>
                <div className="prototype-inline-actions">
                  <button type="button" className="prototype-ghost-button" onClick={() => setScreen("analysis")}>
                    回片段清單
                  </button>
                  <button
                    type="button"
                    className="prototype-primary-button"
                    onClick={() => setPreviewReady(true)}
                  >
                    Render 第一版
                  </button>
                </div>
              </header>

              <div className="prototype-edit-grid">
                <article className="prototype-preview-card">
                  <div className="prototype-preview-frame">
                    <span>9:16 Preview</span>
                    <strong>{previewReady ? "Draft v1 Ready" : "等待 Render"}</strong>
                  </div>
                  <div className="prototype-preview-stats">
                    <span>{segments.length} 段片段</span>
                    <span>{musicSource === "ai" ? "AI 配樂" : "自選 BGM"}</span>
                    <span>{aspectRatio}</span>
                  </div>
                  <button type="button" className="prototype-secondary-button" onClick={() => setScreen("timeline")}>
                    展開進階時間軸細修
                  </button>
                </article>

                <div className="prototype-settings-stack">
                  <article className="prototype-panel">
                    <p className="prototype-panel__eyebrow">配樂設定</p>
                    <div className="prototype-control-grid">
                      <label>
                        音樂來源
                        <select value={musicSource} onChange={(event) => setMusicSource(event.target.value)}>
                          <option value="ai">AI 自動配樂</option>
                          <option value="upload">上傳自己的 BGM</option>
                        </select>
                      </label>
                      <label>
                        音樂氣質
                        <select value={musicTone} onChange={(event) => setMusicTone(event.target.value)}>
                          <option value="冷冽節奏">冷冽節奏</option>
                          <option value="都會明亮">都會明亮</option>
                          <option value="慢板電影感">慢板電影感</option>
                        </select>
                      </label>
                    </div>
                  </article>

                  <article className="prototype-panel">
                    <p className="prototype-panel__eyebrow">輸出設定</p>
                    <div className="prototype-control-grid">
                      <label>
                        比例
                        <select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)}>
                          <option value="9:16">9:16 Reels</option>
                          <option value="16:9">16:9 Landscape</option>
                        </select>
                      </label>
                      <label>
                        字幕風格
                        <select value={subtitleStyle} onChange={(event) => setSubtitleStyle(event.target.value)}>
                          <option value="極簡白字">極簡白字</option>
                          <option value="品牌金色">品牌金色</option>
                          <option value="無字幕">無字幕</option>
                        </select>
                      </label>
                    </div>
                  </article>

                  <article className="prototype-panel">
                    <p className="prototype-panel__eyebrow">版本區</p>
                    <div className="prototype-version-list">
                      <div className={`prototype-version-card${previewReady ? " is-ready" : ""}`}>
                        <strong>v1</strong>
                        <span>{previewReady ? "ready_for_review" : "pending"}</span>
                        <p>這裡只顯示輸出版本與預覽，不再混進 AI 選片流程。</p>
                      </div>
                    </div>
                  </article>
                </div>
              </div>
            </section>
          ) : null}

          {screen === "timeline" ? (
            <section className="prototype-screen">
              <header className="prototype-screen__header">
                <div>
                  <p className="prototype-screen__eyebrow">TimelineEditor</p>
                  <h2>進階細修才來這裡，不是第一輪必經</h2>
                </div>
                <button type="button" className="prototype-ghost-button" onClick={() => setScreen("edit")}>
                  回版本預覽
                </button>
              </header>

              <div className="prototype-timeline-grid">
                <article className="prototype-panel prototype-timeline-canvas">
                  <p className="prototype-panel__eyebrow">時間軸</p>
                  <div className="prototype-time-ruler">
                    <span>00</span>
                    <span>05</span>
                    <span>10</span>
                    <span>15</span>
                  </div>
                  <div className="prototype-track">
                    {segments.map((segment, index) => (
                      <button
                        key={segment.id}
                        type="button"
                        className={`prototype-track-clip${segment.id === selectedSegment?.id ? " is-selected" : ""}`}
                        style={{
                          width: `${Math.max((segment.end - segment.start) * 11, 16)}%`,
                          left: `${index * 21}%`,
                        }}
                        onClick={() => setSelectedSegmentId(segment.id)}
                      >
                        <strong>{segment.label}</strong>
                        <span>{segment.assetName}</span>
                      </button>
                    ))}
                  </div>
                </article>

                <article className="prototype-panel prototype-inspector">
                  <p className="prototype-panel__eyebrow">Inspector</p>
                  {selectedSegment ? (
                    <>
                      <h3>{selectedSegment.label}</h3>
                      <dl>
                        <div>
                          <dt>素材</dt>
                          <dd>{selectedSegment.assetName}</dd>
                        </div>
                        <div>
                          <dt>片段範圍</dt>
                          <dd>
                            {formatSeconds(selectedSegment.start)} - {formatSeconds(selectedSegment.end)}
                          </dd>
                        </div>
                        <div>
                          <dt>AI 理由</dt>
                          <dd>{selectedSegment.reason}</dd>
                        </div>
                      </dl>
                    </>
                  ) : (
                    <p className="prototype-note">選一段片段看細節。</p>
                  )}
                </article>
              </div>
            </section>
          ) : null}
        </div>
      </section>
    </main>
  );
}
