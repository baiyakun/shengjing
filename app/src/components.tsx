import type { ReactNode } from "react";
import { useEngine, type ModelInfo, type Task } from "./engine";
import { pickFile, AUDIO_FILTER } from "./files";

export function Page({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="page">
      <header className="page-head">
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </header>
      {children}
    </div>
  );
}

export function Section({ title, children, actions }: {
  title?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Field({ label, hint, children }: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function FileField({ label, value, onChange, filter = AUDIO_FILTER, hint }: {
  label: string;
  value: string;
  onChange: (path: string) => void;
  filter?: typeof AUDIO_FILTER;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <div className="row">
        <input value={value} onChange={(e) => onChange(e.target.value)} placeholder="选择文件…" />
        <button
          type="button"
          onClick={async () => {
            const path = await pickFile(filter);
            if (path) onChange(path);
          }}
        >
          选择
        </button>
      </div>
    </Field>
  );
}

const STATUS_LABEL: Record<Task["status"], string> = {
  queued: "排队中",
  running: "进行中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "已取消",
};

export function TaskProgress({ task, error }: { task: Task | null; error?: string | null }) {
  const engine = useEngine();
  if (error) return <p className="error">{error}</p>;
  if (!task) return null;
  const active = task.status === "queued" || task.status === "running";
  return (
    <div className={`task task-${task.status}`}>
      <div className="row between">
        <span>
          <b>{STATUS_LABEL[task.status]}</b> {task.message && <span className="muted">· {task.message}</span>}
        </span>
        {active && (
          <button className="ghost" onClick={() => engine.post(`/tasks/${task.id}/cancel`)}>
            取消
          </button>
        )}
      </div>
      {active && (
        <div className="progress">
          <div style={{ width: `${Math.round(task.progress * 100)}%` }} />
        </div>
      )}
      {task.status === "failed" && task.error && <p className="error">{task.error}</p>}
    </div>
  );
}

export function Player({ path, label }: { path: string; label?: string }) {
  const engine = useEngine();
  return (
    <div className="player">
      {label && <div className="muted small">{label}</div>}
      <audio controls preload="metadata" src={engine.fileUrl(path)} />
      <div className="path small muted" title={path}>
        {path}
      </div>
    </div>
  );
}

export function Warnings({ items }: { items?: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="warnings">
      {items.map((w) => (
        <li key={w}>{w}</li>
      ))}
    </ul>
  );
}

const COMMERCIAL_LABEL = { yes: "可商用", restricted: "有条件商用", no: "非商用" } as const;

export function LicenseBadge({ model }: { model: Pick<ModelInfo, "commercial" | "license"> }) {
  return (
    <span className={`badge badge-${model.commercial}`} title={model.license}>
      {COMMERCIAL_LABEL[model.commercial]}
    </span>
  );
}

/** 模型的简短说明：本地模型显示内存和许可，在线 API 显示是否已填 Key。 */
export function modelMeta(m: ModelInfo): string {
  if (m.worker === "cloud") {
    return m.worker_installed ? "在线 API" : "在线 API · 未填写 API Key";
  }
  const parts = [`约 ${m.memory_gb} GB`, COMMERCIAL_LABEL[m.commercial]];
  if (m.needs_conversion) parts.push("需自行转换权重");
  if (!m.worker_installed) parts.push("扩展包未安装");
  return parts.join(" · ");
}

/** 当前选择（或默认）的模型。 */
export function resolveModel(
  models: ModelInfo[] | null, kinds: string[], chosen: string,
): ModelInfo | undefined {
  const list = (models ?? []).filter((m) => kinds.includes(m.kind));
  return chosen ? list.find((m) => m.id === chosen) : list.find((m) => m.default);
}

/** 某类任务可选的模型（只列当前版本允许使用的），本地和在线 API 都在里面。 */
export function ModelSelect({ models, kinds, value, onChange }: {
  models: ModelInfo[] | null;
  kinds: string[];
  value: string;
  onChange: (id: string) => void;
}) {
  const options = (models ?? []).filter((m) => kinds.includes(m.kind) && m.allowed);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">默认</option>
      {options.map((m) => (
        <option key={m.id} value={m.id}>
          {m.name}
          {m.default ? "（默认）" : ""} · {modelMeta(m)}
        </option>
      ))}
    </select>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted empty">{children}</p>;
}
