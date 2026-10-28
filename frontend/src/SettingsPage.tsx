import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  Clock3,
  FlaskConical,
  GitBranch,
  Loader2,
  Plug,
  RotateCcw,
  Save,
  Settings2,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { api, post, put } from "./api";
import type { Config, ModelConfig } from "./types";
import { maintenanceInputLimit } from "./maintenanceUi";
import "./maintenance.css";
import { FocusSettings } from "./FocusSettings";
import { RetrievalSettings } from "./RetrievalSettings";

type Props = {
  sid: string;
  config: Config;
  model: ModelConfig;
  onConfig: (c: Config) => void;
  onModel: (m: ModelConfig) => void;
  notify: (s: string) => void;
  onError: (e: unknown) => void;
};
type NumericKey = {
  [K in keyof Config]: Config[K] extends number ? K : never;
}[keyof Config];
type SliderDef = {
  key: NumericKey;
  label: string;
  description: string;
  min: number;
  max: number;
  step: number;
  suffix?: string;
};
const memoryFields: SliderDef[] = [
  {
    key: "half_life_days",
    label: "基础半衰期",
    description: "普通记忆的初始淡忘速度；鲜明经历会获得更长的保持时间。",
    min: 1,
    max: 180,
    step: 1,
    suffix: "天",
  },
  {
    key: "salience_multiplier",
    label: "印象对保持时间的影响",
    description: "越高，初始印象鲜明的经历越不容易随时间淡去。",
    min: 0,
    max: 20,
    step: 0.5,
    suffix: "倍",
  },
  {
    key: "reinforcement_boost",
    label: "真实重提的强化幅度",
    description: "仅由有来源的再次谈起增加强度，后台读取不强化。",
    min: 0,
    max: 1,
    step: 0.01,
  },
  {
    key: "activation_threshold",
    label: "唤起门槛",
    description: "降低后会有更多事情浮现；提高后只留下更明确的关联。",
    min: 0.01,
    max: 0.9,
    step: 0.01,
  },
];
const weightFields: SliderDef[] = [
  {
    key: "cue_weight",
    label: "情境线索权重",
    description: "当前人物之外的时间、场景、细节与内容线索。",
    min: 0,
    max: 1,
    step: 0.01,
  },
  {
    key: "retention_weight",
    label: "记忆鲜明度权重",
    description: "记忆当前有多鲜明，对入口选择的影响。",
    min: 0,
    max: 1,
    step: 0.01,
  },
  {
    key: "person_weight",
    label: "对话者关联权重",
    description: "与当前交流对象的共同经历；未配置关注对象时，此项不参与评分。",
    min: 0,
    max: 1,
    step: 0.01,
  },
];
const graphFields: SliderDef[] = [
  {
    key: "retrieval_limit",
    label: "多路候选上限",
    description: "从全文、锚点、经历结构和人物等方向筛选；候选不会全部注入。",
    min: 12,
    max: 120,
    step: 1,
    suffix: "件",
  },
  {
    key: "model_catalog_char_budget",
    label: "模型候选目录预算",
    description: "限制交给模型判断的目录大小；超出的候选会在接口中报告。",
    min: 8000,
    max: 180000,
    step: 1000,
    suffix: "字符",
  },
  {
    key: "graph_candidate_limit",
    label: "关联展开候选上限",
    description:
      "限制递归读取的候选规模，实际注入仍受激活阈值和上下文预算限制。",
    min: 120,
    max: 2000,
    step: 10,
    suffix: "件",
  },
  {
    key: "propagation_decay",
    label: "关联传播保留比例",
    description:
      "每经过一条链接保留多少激活强度，同时受连接强度和目标记忆状态影响。",
    min: 0.05,
    max: 0.99,
    step: 0.01,
  },
  {
    key: "max_depth",
    label: "最大递归深度",
    description: "限制沿关联继续寻找的层数；循环和重复事件会自动去重。",
    min: 0,
    max: 8,
    step: 1,
    suffix: "层",
  },
  {
    key: "max_seeds",
    label: "入口事件上限",
    description: "从多路候选中选出的入口数量。",
    min: 1,
    max: 12,
    step: 1,
    suffix: "件",
  },
  {
    key: "max_events",
    label: "上下文事件上限",
    description: "本轮实际注入的事件总数，包含关联展开的事件。",
    min: 1,
    max: 30,
    step: 1,
    suffix: "件",
  },
  {
    key: "context_char_budget",
    label: "上下文字符预算",
    description: "预算不足时跳过整件事件，并在召回结果中报告；保留完整正文。",
    min: 1000,
    max: 100000,
    step: 1000,
    suffix: "字符",
  },
];

export function SettingsPage({
  sid,
  config,
  model,
  onConfig,
  onModel,
  notify,
  onError,
}: Props) {
  const [tab, setTab] = useState("memory"),
    [draft, setDraft] = useState(config),
    [connection, setConnection] = useState(model),
    [key, setKey] = useState(""),
    [saving, setSaving] = useState(false),
    [testing, setTesting] = useState(false),
    [testResult, setTestResult] = useState("");
  useEffect(() => {
    setDraft(config);
  }, [config]);
  useEffect(() => {
    setConnection(model);
    setKey("");
  }, [model]);
  const dirty = JSON.stringify(config) !== JSON.stringify(draft);
  async function save() {
    setSaving(true);
    try {
      const value = await put<Config>(`/spaces/${sid}/config`, draft);
      onConfig(value);
      notify("记忆配置已保存，下一次整理或唤起时生效");
    } catch (e) {
      onError(e);
    } finally {
      setSaving(false);
    }
  }
  async function saveModel(test = false) {
    setSaving(true);
    setTestResult("");
    try {
      const result = await put<ModelConfig>(`/spaces/${sid}/model`, {
        mode: connection.mode,
        base_url: connection.base_url,
        model: connection.model,
        timeout_seconds: connection.timeout_seconds ?? 60,
        max_retries: connection.max_retries ?? 2,
        context_window_tokens: connection.context_window_tokens ?? null,
        maintenance_output_tokens: connection.maintenance_output_tokens ?? 4096,
        ...(key ? { api_key: key } : {}),
      });
      onModel(result);
      notify("模型配置已保存");
      if (test) {
        setTesting(true);
        const response = await post<{ message: string; elapsed_ms?: number }>(
          `/spaces/${sid}/model/test`,
          {},
        );
        setTestResult(
          response.message +
            (response.elapsed_ms ? ` · ${response.elapsed_ms} ms` : ""),
        );
      }
    } catch (e) {
      onError(e);
    } finally {
      setSaving(false);
      setTesting(false);
    }
  }
  function field(def: SliderDef) {
    return (
      <div className="setting-row" key={def.key}>
        <div>
          <label htmlFor={def.key}>{def.label}</label>
          <p>{def.description}</p>
        </div>
        <div className="setting-control">
          <input
            id={def.key}
            aria-label={def.label}
            type="range"
            min={def.min}
            max={def.max}
            step={def.step}
            value={draft[def.key]}
            onChange={(e) =>
              setDraft((d) => ({ ...d, [def.key]: Number(e.target.value) }))
            }
          />
          <label>
            <input
              aria-label={`${def.label}数值`}
              type="number"
              min={def.min}
              max={def.max}
              step={def.step}
              value={draft[def.key]}
              onChange={(e) =>
                setDraft((d) => ({ ...d, [def.key]: Number(e.target.value) }))
              }
            />
            {def.suffix && <span>{def.suffix}</span>}
          </label>
        </div>
      </div>
    );
  }
  const preview = (salience: number, days: number) =>
    (0.18 + 0.82 * salience) *
    2 **
      (-days /
        (draft.half_life_days *
          (1 + draft.salience_multiplier * salience ** 2)));
  return (
    <>
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <span />
            TUNE THE WAY MEMORY WORKS
          </div>
          <h1>
            记住什么，怎样淡去<span>。</span>
          </h1>
          <p>让参数透明，让每次唤起都有可观察的依据。</p>
        </div>
        {!["model", "retrieval", "focus"].includes(tab) && (
          <div className="heading-actions">
            {dirty && <span className="unsaved-dot">有未保存的更改</span>}
            <button
              className="button primary"
              disabled={saving || !dirty}
              onClick={save}
            >
              {saving ? (
                <Loader2 size={15} className="spin" />
              ) : (
                <Save size={15} />
              )}
              保存配置
            </button>
          </div>
        )}
      </section>
      <div className="settings-tabs">
        {[
          { id: "focus", label: "关注对象", icon: Sparkles },
          { id: "memory", label: "唤起与淡忘", icon: Sparkles },
          { id: "retrieval", label: "召回方式", icon: SlidersHorizontal },
          { id: "graph", label: "关联展开", icon: GitBranch },
          { id: "maintenance", label: "整理与提示词", icon: Clock3 },
          { id: "model", label: "模型连接", icon: Plug },
        ].map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            <t.icon size={16} />
            {t.label}
          </button>
        ))}
      </div>
      {tab === "focus" && <FocusSettings sid={sid} config={config} onConfig={onConfig} notify={notify} onError={onError}/> }
      {tab === "retrieval" && (
        <RetrievalSettings
          key={sid}
          sid={sid}
          config={draft}
          onConfig={(value) => {
            setDraft(value);
            onConfig(value);
          }}
          notify={notify}
          onError={onError}
        />
      )}
      {tab === "memory" && (
        <div className="settings-layout">
          <div>
            <section className="panel settings-section">
              <div className="settings-section-heading">
                <div>
                  <h3>记忆随时间的变化</h3>
                  <p>同样的时间间隔，可以留下不同程度的印象。</p>
                </div>
                <Clock3 size={19} />
              </div>
              {memoryFields.map(field)}
            </section>
            <section className="panel settings-section">
              <div className="settings-section-heading">
                <div>
                  <h3>当前情境如何唤起记忆</h3>
                  <p>各权重按总和归一化，激活分不是概率，不要求总和为 1。</p>
                </div>
                <SlidersHorizontal size={19} />
              </div>
              {weightFields.map(field)}
            </section>
          </div>
          <aside className="panel preview-card">
            <span className="eyebrow">PARAMETER PREVIEW</span>
            <h3>时间留下的曲线</h3>
            <p>
              未保存参数也可预览。假设初始印象分别为 0.9 与
              0.2，之后没有真实重提。
            </p>
            <svg
              viewBox="0 0 300 170"
              role="img"
              aria-label="不同初始印象的衰减曲线"
            >
              <path
                d="M25 15V145H290M25 80H290M25 15H290"
                fill="none"
                stroke="#e9e6de"
              />
              {[0.9, 0.2].map((s, i) => (
                <path
                  key={s}
                  d={
                    "M" +
                    Array.from(
                      { length: 61 },
                      (_, n) =>
                        `${25 + (n / 60) * 265},${145 - preview(s, (n / 60) * 180) * 130}`,
                    ).join(" L")
                  }
                  fill="none"
                  stroke={i === 0 ? "#b86c47" : "#9aab9d"}
                  strokeWidth="2.5"
                />
              ))}
              <text x="25" y="165">
                今天
              </text>
              <text x="145" y="165">
                90天
              </text>
              <text x="265" y="165">
                180天
              </text>
            </svg>
            <div className="line-legend">
              <span>
                <i />
                鲜明经历
              </span>
              <span>
                <i />
                普通片段
              </span>
            </div>
            <div className="preview-footnote">
              <FlaskConical size={15} />
              这是可调的工程模型，不代表人类记忆的测量结果。
            </div>
          </aside>
        </div>
      )}
      {tab === "graph" && (
        <section className="panel settings-section">
          <div className="settings-section-heading">
            <div>
              <h3>从一件事展开关联</h3>
              <p>传播会逐渐减弱，避免一条线索带回整个档案。</p>
            </div>
            <GitBranch size={20} />
          </div>
          {graphFields.map(field)}
          <div className="setting-row">
            <div>
              <label>模型扩展联想线索</label>
              <p>
                仅在显式选择大模型联想时使用，先分析经历结构再检索；增加一次模型调用。
              </p>
            </div>
            <button
              className={`switch ${draft.query_expansion_enabled ? "on" : ""}`}
              role="switch"
              aria-label="模型扩展联想线索"
              aria-checked={draft.query_expansion_enabled}
              onClick={() =>
                setDraft((d) => ({
                  ...d,
                  query_expansion_enabled: !d.query_expansion_enabled,
                }))
              }
            >
              <span />
            </button>
          </div>
          <div className="setting-row">
            <div>
              <label>沿反向引用展开</label>
              <p>A 引用 B 时，从 B 也可以尝试唤起 A。</p>
            </div>
            <button
              className={`switch ${draft.backlinks ? "on" : ""}`}
              role="switch"
              aria-label="沿反向引用展开"
              aria-checked={draft.backlinks}
              onClick={() =>
                setDraft((d) => ({ ...d, backlinks: !d.backlinks }))
              }
            >
              <span />
            </button>
          </div>
        </section>
      )}
      {tab === "maintenance" && (
        <>
          <section className="panel settings-section">
            <div className="settings-section-heading">
              <div>
                <h3>整理的触发方式</h3>
                <p>也可以随时在实验室手动整理，或由外部 agent 调用接口。</p>
              </div>
              <Clock3 size={20} />
            </div>
            <div className="maintenance-budget-intro"><Sparkles size={21} /><div><strong>{model.context_window_tokens ? `已配置上下文 ${model.context_window_tokens.toLocaleString()} token` : "模型容量未知 · 使用保守预算"}</strong><p>当前输入上限 {Math.max(0, maintenanceInputLimit(draft, model)).toLocaleString()} token；统一计入提示词、新对话、旧事材料和人物资料。按 UTF-8 保守估算，首次请求仍可能超限。容量与输出预留可在<button className="text-button" onClick={() => setTab("model")}>模型连接</button>中设置。</p></div></div>
            {field({ key: "maintenance_input_token_budget", label: "整理输入预算", description: "容量未知时使用此预算；已配置容量时，还会扣除输出和安全预留。", min: 1024, max: 128000, step: 64, suffix: "token" })}
            {field({ key: "maintenance_safety_tokens", label: "安全余量", description: "已配置上下文容量时额外预留，补偿估算与提供方包装差异。", min: 128, max: 8192, step: 128, suffix: "token" })}
            {field({ key: "maintenance_max_adaptations", label: "超限后最多缩批次数", description: "明确上下文超限或输出达到上限时，缩小批次再试；鉴权和普通参数错误不会触发。", min: 0, max: 6, step: 1, suffix: "次" })}
            {field({ key: "maintenance_max_read_rounds", label: "旧事补读轮次", description: "模型可按原文位置请求必要材料；放不下或轮次用尽时暂缓，不凭片段强行修改。", min: 0, max: 4, step: 1, suffix: "轮" })}
            {field({ key: "maintenance_forgetting_weight", label: "整理时的遗忘权重", description: "越高，淡忘且线索弱的旧事越少进入整理上下文。明确细节仍可唤起旧事；正文与事实可信度不变。", min: 0, max: 1, step: 0.01 })}
            {field({
              key: "maintenance_batch_chars",
              label: "每批整理字符上限",
              description:
                "作为批次外层上限；优先完整消息，单条过长时按可追溯位置分段，成功提交后推进。",
              min: 20000,
              max: 100000,
              step: 1000,
              suffix: "字符",
            })}
            <div className="setting-row">
              <div>
                <label>根据真实重提强化记忆</label>
                <p>
                  由整理模型提出，后端核对用户原话、事件细节与来源。检索和助手复述不会强化。
                </p>
              </div>
              <button
                className={`switch ${draft.auto_reinforcement_enabled ? "on" : ""}`}
                role="switch"
                aria-label="根据真实重提强化记忆"
                aria-checked={draft.auto_reinforcement_enabled}
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    auto_reinforcement_enabled: !d.auto_reinforcement_enabled,
                  }))
                }
              >
                <span />
              </button>
            </div>
            <div className="setting-row">
              <div>
                <label>自动整理</label>
                <p>由服务进程按真实时间运行；实验时间不会改变任务调度。</p>
              </div>
              <button
                className={`switch ${draft.schedule_enabled ? "on" : ""}`}
                role="switch"
                aria-label="自动整理"
                aria-checked={draft.schedule_enabled}
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    schedule_enabled: !d.schedule_enabled,
                  }))
                }
              >
                <span />
              </button>
            </div>
            <div className="schedule-form">
              <label className="field-label">
                触发模式
                <select
                  value={draft.schedule_mode}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, schedule_mode: e.target.value }))
                  }
                >
                  <option value="interval">固定间隔</option>
                  <option value="daily">每天指定时间</option>
                </select>
              </label>
              {draft.schedule_mode === "interval" ? (
                <label className="field-label">
                  间隔（分钟）
                  <input
                    type="number"
                    min="1"
                    max="10080"
                    value={draft.schedule_interval_minutes}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        schedule_interval_minutes: Number(e.target.value),
                      }))
                    }
                  />
                </label>
              ) : (
                <label className="field-label">
                  每天的触发时刻
                  <input
                    type="time"
                    value={draft.schedule_daily_time}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        schedule_daily_time: e.target.value,
                      }))
                    }
                  />
                </label>
              )}
              <label className="field-label">
                时区
                <select
                  value={draft.timezone}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, timezone: e.target.value }))
                  }
                >
                  <option>Asia/Shanghai</option>
                  <option>UTC</option>
                  <option>Asia/Tokyo</option>
                  <option>America/New_York</option>
                  <option>Europe/London</option>
                </select>
              </label>
            </div>
          </section>
          <section className="panel settings-section prompt-section">
            <div className="settings-section-heading">
              <div>
                <h3>事件维护提示词</h3>
                <p>模型整理时使用。结构字段由后端验证，原始对话保留来源。</p>
              </div>
              <Settings2 size={19} />
            </div>
            <textarea
              aria-label="事件维护提示词"
              value={draft.maintenance_prompt}
              onChange={(e) =>
                setDraft((d) => ({ ...d, maintenance_prompt: e.target.value }))
              }
              rows={14}
            />
          </section>
          <section className="panel settings-section prompt-section">
            <div className="settings-section-heading">
              <div>
                <h3>模拟对话的 system prompt</h3>
                <p>
                  只用于实验室的对话模型，记忆 context
                  接口不会附带这段回应策略。
                </p>
              </div>
              <FlaskConical size={19} />
            </div>
            <textarea
              aria-label="模拟对话提示词"
              value={draft.simulator_prompt}
              onChange={(e) =>
                setDraft((d) => ({ ...d, simulator_prompt: e.target.value }))
              }
              rows={5}
            />
          </section>
        </>
      )}
      {tab === "model" && (
        <div className="settings-layout">
          <section className="panel settings-section model-section">
            <div className="settings-section-heading">
              <div>
                <h3>模型服务连接</h3>
                <p>后端统一调用兼容 Chat Completions 的 HTTP 接口。</p>
              </div>
              <Plug size={20} />
            </div>
            <div className="model-mode-options">
              <label className={connection.mode === "local" ? "selected" : ""}>
                <input
                  type="radio"
                  name="model-mode"
                  checked={connection.mode === "local"}
                  onChange={() =>
                    setConnection((c) => ({ ...c, mode: "local" }))
                  }
                />
                <span>
                  <strong>本地演示</strong>
                  <small>整理仅做原文归档，对话使用模板回放</small>
                </span>
              </label>
              <label className={connection.mode === "llm" ? "selected" : ""}>
                <input
                  type="radio"
                  name="model-mode"
                  checked={connection.mode === "llm"}
                  onChange={() => setConnection((c) => ({ ...c, mode: "llm" }))}
                />
                <span>
                  <strong>连接模型</strong>
                  <small>
                    用于事件整理与对话；大模型联想在召回设置中另行开启
                  </small>
                </span>
              </label>
            </div>
            <label className="field-label">
              API Base URL
              <input
                type="url"
                value={connection.base_url}
                onChange={(e) =>
                  setConnection((c) => ({ ...c, base_url: e.target.value }))
                }
                placeholder="https://your-model-service/v1"
              />
            </label>
            <label className="field-label">
              模型名称
              <input
                value={connection.model}
                onChange={(e) =>
                  setConnection((c) => ({ ...c, model: e.target.value }))
                }
                placeholder="填写模型服务支持的模型 ID"
              />
            </label>
            <div className="schedule-form">
              <label className="field-label">上下文容量（可选，token）<input type="number" min="2048" max="10000000" placeholder="未知，留空也可使用" value={connection.context_window_tokens ?? ""} onChange={e => setConnection(c => ({ ...c, context_window_tokens: e.target.value === "" ? null : Number(e.target.value) }))} /></label>
              <label className="field-label">整理输出预留（token）<input type="number" min="256" max="100000" value={connection.maintenance_output_tokens ?? 4096} onChange={e => setConnection(c => ({ ...c, maintenance_output_tokens: Number(e.target.value) }))} /></label>
            </div>
            <p className="subtle-note">填写当前服务实际开放的容量，保存连接后生效。容量不会根据模型名称自动猜测；输出预留仅用于事件整理。</p>
            <label className="field-label">
              API Key
              <small>
                {model.has_key
                  ? "服务端已有密钥，留空保持不变"
                  : "密钥仅保存在服务进程中，不返回浏览器"}
              </small>
              <input
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={
                  model.has_key
                    ? "•••••••• 已配置"
                    : "输入密钥；本地免认证模型可留空"
                }
              />
            </label>
            <p className="subtle-note">
              页面填写的密钥在服务重启后需重新设置。部署时可通过 MODEL_API_KEY
              环境变量提供。
            </p>
            <div className="schedule-form">
              <label className="field-label">
                单次调用超时（秒）
                <input
                  type="number"
                  min="1"
                  max="180"
                  value={connection.timeout_seconds ?? 60}
                  onChange={(e) =>
                    setConnection((c) => ({
                      ...c,
                      timeout_seconds: Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label className="field-label">
                临时错误重试次数
                <input
                  type="number"
                  min="0"
                  max="3"
                  value={connection.max_retries ?? 2}
                  onChange={(e) =>
                    setConnection((c) => ({
                      ...c,
                      max_retries: Number(e.target.value),
                    }))
                  }
                />
              </label>
            </div>
            <div className="model-actions">
              <button
                className="button secondary"
                disabled={saving}
                onClick={() => saveModel(true)}
              >
                {testing ? (
                  <Loader2 size={15} className="spin" />
                ) : (
                  <Plug size={15} />
                )}
                保存并测试
              </button>
              <button
                className="button primary"
                disabled={saving}
                onClick={() => saveModel()}
              >
                <Save size={15} />
                保存连接
              </button>
            </div>
            {testResult && (
              <div className="success-note">
                <Check size={15} />
                {testResult}
              </div>
            )}
          </section>
          <aside className="panel connection-note">
            <span className="eyebrow">ONE MODEL, THREE USES</span>
            <h3>在一处连接</h3>
            <div>
              <span>01</span>
              <p>
                <strong>整理经历</strong>从真实对话维护详细事件与锚点。
              </p>
            </div>
            <div>
              <span>02</span>
              <p>
                <strong>可选联想</strong>仅显式开启时理解情境与事件的联系。
              </p>
            </div>
            <div>
              <span>03</span>
              <p>
                <strong>模拟对话</strong>在实验室观察注入记忆后的回应。
              </p>
            </div>
            <small>
              模型需支持 JSON
              对象输出。模型错误会明确报告，整理失败不会提前推进消息处理进度。
            </small>
          </aside>
        </div>
      )}
    </>
  );
}
