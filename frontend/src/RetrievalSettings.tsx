import { useEffect, useState } from "react";
import { Save, SlidersHorizontal } from "lucide-react";
import { api, put } from "./api";
import type { Capabilities, Config } from "./types";
import { retrievalModes } from "./retrievalUi";
import "./retrieval.css";

export function RetrievalSettings({sid, config, onConfig, notify, onError}: {
  sid: string; config: Config; onConfig: (c: Config) => void;
  notify: (s: string) => void; onError: (e: unknown) => void;
}) {
  const [draft, setDraft] = useState(config);
  const [working, setWorking] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  useEffect(() => { setDraft(config); }, [config]);
  useEffect(() => {
    let cancelled = false;
    api<Capabilities>(`/spaces/${sid}/capabilities`).then(value => {
      if (!cancelled) setCapabilities(value);
    }).catch(onError);
    return () => { cancelled = true; };
  }, [sid]);
  async function saveRecall() {
    setWorking(true);
    try { onConfig(await put<Config>(`/spaces/${sid}/config`, draft)); notify("召回配置已保存"); }
    catch (e) { onError(e); } finally { setWorking(false); }
  }
  return (
    <div className="retrieval-settings">
      <section className="panel settings-section">
        <div className="settings-section-heading">
          <div>
            <h3>每轮对话，怎样找到入口</h3>
            <p>召回与聊天、整理分别选择。默认无需额外模型配置。</p>
          </div>
          <SlidersHorizontal size={20} />
        </div>
        <div className="retrieval-mode-grid">
          {retrievalModes.map((mode) => (
            <label
              key={mode.id}
              className={`retrieval-mode-card ${draft.default_retrieval_mode === mode.id ? "selected" : ""}`}
            >
              <input
                type="radio"
                name="default-retrieval-mode"
                value={mode.id}
                checked={draft.default_retrieval_mode === mode.id}
                onChange={() =>
                  setDraft((old) => ({
                    ...old,
                    default_retrieval_mode: mode.id,
                  }))
                }
              />
              <span>
                <strong>
                  {mode.name}
                  {mode.id === "local" && <small>推荐</small>}
                </strong>
                <p>{mode.note}</p>
                {capabilities &&
                  !capabilities.retrieval_modes[mode.id].available && (
                    <em>{capabilities.retrieval_modes[mode.id].reason}</em>
                  )}
              </span>
            </label>
          ))}
        </div>
        {(
          [
            ["lexical", "文字与锚点", "原话、独特细节、场景及已有结构线索。"],
            [
              "person_salience",
              "人物与共同历史",
              "与当前人物相关、仍然鲜明的经历。",
            ],
            [
              "temporal",
              "时间与计划日期",
              "纪念日、时间地标和当天的未来计划。",
            ],
          ] as const
        ).map(([id, title, note]) => (
          <div className="setting-row" key={id}>
            <div>
              <label>{title}</label>
              <p>{note}</p>
            </div>
            <button
              className={`switch ${draft.retrieval_routes[id] ? "on" : ""}`}
              role="switch"
              aria-label={title}
              aria-checked={draft.retrieval_routes[id]}
              onClick={() =>
                setDraft((old) => ({
                  ...old,
                  retrieval_routes: {
                    ...old.retrieval_routes,
                    [id]: !old.retrieval_routes[id],
                  },
                }))
              }
            >
              <span />
            </button>
          </div>
        ))}
        <div className="retrieval-actions">
          <p className="subtle-note">
            沿事件链接的展开深度、入口数量与原文预算，在「关联展开」中调整。
          </p>
          <button
            className="button primary"
            disabled={
              !!working || JSON.stringify(draft) === JSON.stringify(config)
            }
            onClick={saveRecall}
          >
            <Save size={15} />
            保存召回配置
          </button>
        </div>
      </section>
    </div>
  );
}
