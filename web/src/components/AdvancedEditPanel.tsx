import { type ReactNode } from "react";
import "./AdvancedEditPanel.css";

export type AdvancedTab = "settings" | "timeline" | "subtitles";

interface AdvancedEditPanelProps {
  activeTab: AdvancedTab;
  onTabChange: (tab: AdvancedTab) => void;
  settingsContent: ReactNode;
  timelineContent: ReactNode;
  subtitlesContent: ReactNode;
}

const TABS: { id: AdvancedTab; label: string }[] = [
  { id: "settings", label: "⚙ 設定" },
  { id: "timeline", label: "🎞 時間軸" },
  { id: "subtitles", label: "💬 字幕" },
];

export default function AdvancedEditPanel({
  activeTab,
  onTabChange,
  settingsContent,
  timelineContent,
  subtitlesContent,
}: AdvancedEditPanelProps) {
  return (
    <div className="adv-panel">
      <div className="adv-panel__tab-bar" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={activeTab === t.id}
            className={[
              "adv-panel__tab",
              activeTab === t.id ? "adv-panel__tab--active" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            onClick={() => onTabChange(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        className={[
          "adv-panel__body",
          activeTab === "timeline" ? "adv-panel__body--fullwidth" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        role="tabpanel"
      >
        {activeTab === "settings" && settingsContent}
        {activeTab === "timeline" && timelineContent}
        {activeTab === "subtitles" && subtitlesContent}
      </div>
    </div>
  );
}
