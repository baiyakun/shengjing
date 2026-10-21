// 界面组件库：所有页面共用的按钮、卡片、表单、状态、弹窗等
import {
  Ban, CircleCheck, CircleX, Clock, FileAudio, LoaderCircle, TriangleAlert, Upload, X,
  type LucideIcon,
} from "lucide-react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import {
  useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode,
} from "react";
import { inTauri, useEngine, type ModelInfo, type Task } from "./engine";
import { AUDIO_FILTER, pickFile } from "./files";
import { APPS, type PageId } from "./nav";

export { Player } from "./player";

// ---------------- 布局 ----------------

export function Tile({ app, size = "sm" }: { app: PageId; size?: "sm" | "md" | "lg" }) {
  const meta = APPS[app];
  const Icon = meta.icon;
  const px = size === "lg" ? 24 : size === "md" ? 18 : 13;
  return (
    <span className={`tile ${size}`} style={{ background: tileGradient(meta.tint) }}>
      <Icon size={px} strokeWidth={2.2} />
    </span>
  );
}

export function tileGradient(tint: string) {
  return `linear-gradient(180deg, color-mix(in srgb, ${tint} 78%, white), ${tint})`;
}

export function Page({ app, title, subtitle, actions, narrow, children }: {
  app?: PageId;
  title?: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  narrow?: boolean;
  children: ReactNode;
}) {
  const meta = app ? APPS[app] : null;
  return (
    <div className={`page ${narrow ? "narrow" : ""}`}>
      <header className="page-head">
        {app && <Tile app={app} size="lg" />}
        <div className="grow">
          <h1>{title ?? meta?.label}</h1>
          {(subtitle ?? meta?.desc) && <p>{subtitle ?? meta?.desc}</p>}
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </header>
      {children}
    </div>
  );
}

export function Card({ title, sub, step, done, actions, flush, children, className = "" }: {
  title?: ReactNode;
  sub?: ReactNode;
  step?: number;
  done?: boolean;
  actions?: ReactNode;
  flush?: boolean;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${flush ? "flush" : ""} ${className}`}>
      {(title || actions) && (
        <div className="card-head" style={flush ? { padding: "16px 18px 0" } : undefined}>
          {step !== undefined && <span className={`step-no ${done ? "done" : ""}`}>{step}</span>}
          <div className="grow">
            {title && <h2>{title}</h2>}
            {sub && <div className="sub">{sub}</div>}
          </div>
          {actions && <div className="btn-row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Empty({ icon: Icon = FileAudio, title, children }: {
  icon?: LucideIcon;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon size={22} /></div>
      {title && <b>{title}</b>}
      {children && <div className="small">{children}</div>}
    </div>
  );
}

export function Callout({ tone = "warning", children }: {
  tone?: "warning" | "danger" | "info";
  children: ReactNode;
}) {
  return (
    <div className={`callout ${tone === "warning" ? "" : tone}`}>
      <TriangleAlert size={16} />
      <div>{children}</div>
    </div>
  );
}

// ---------------- 按钮 ----------------

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger" | "default";
  size?: "sm" | "lg";
  icon?: LucideIcon;
  loading?: boolean;
};

export function Button({ variant = "default", size, icon: Icon, loading, children, className = "",
  ...rest }: BtnProps) {
  const cls = ["btn", variant !== "default" ? variant : "", size ?? "", !children ? "icon" : "",
    className].join(" ");
  const px = size === "lg" ? 17 : 15;
  return (
    <button type="button" className={cls} {...rest} disabled={rest.disabled || loading}>
      {loading ? <LoaderCircle size={px} className="spin" /> : Icon && <Icon size={px} />}
      {children}
    </button>
  );
}

// ---------------- 表单 ----------------

export function Field({ label, hint, children }: {
  label: ReactNode;
  hint?: ReactNode;
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

export function Toggle({ checked, onChange, label, hint, disabled }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
      <span className="toggle-text">
        <span>{label}</span>
        {hint && <small>{hint}</small>}
      </span>
    </label>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, block }: {
  value: T;
  options: { value: T; label: ReactNode; icon?: LucideIcon }[];
  onChange: (v: T) => void;
  block?: boolean;
}) {
  return (
    <div className={`segmented ${block ? "block" : ""}`} role="radiogroup">
      {options.map((o) => {
        const Icon = o.icon;
        return (
          <button key={String(o.value)} type="button" role="radio" aria-checked={o.value === value}
            className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
            {Icon && <Icon size={14} />}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** 选择本机文件：点击选择，或在 App 里直接把文件拖进来。 */
export function DropZone({ value, onChange, filter = AUDIO_FILTER, label = "选择音频文件",
  hint = "点击选择，或把文件拖到这里" }: {
  value: string;
  onChange: (path: string) => void;
  filter?: typeof AUDIO_FILTER;
  label?: string;
  hint?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [over, setOver] = useState(false);

  useEffect(() => {
    if (!inTauri) return;
    let unlisten: (() => void) | undefined;
    const inside = (pos?: { x: number; y: number }) => {
      const el = ref.current;
      if (!el || !pos) return false;
      const r = el.getBoundingClientRect();
      const x = pos.x / window.devicePixelRatio;
      const y = pos.y / window.devicePixelRatio;
      return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    };
    // 只接受这个拖放区支持的文件类型
    const accepted = (path: string) =>
      filter.extensions.includes(path.split(".").pop()?.toLowerCase() ?? "");
    let cancelled = false;
    getCurrentWebview()
      .onDragDropEvent((event) => {
        const p = event.payload;
        if (p.type === "over") setOver(inside(p.position));
        else if (p.type === "leave") setOver(false);
        else if (p.type === "drop") {
          setOver(false);
          const file = p.paths.find(accepted);
          if (inside(p.position) && file) onChange(file);
        }
      })
      .then((fn) => {
        // 页面已经切走了就马上取消监听
        if (cancelled) fn();
        else unlisten = fn;
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [onChange, filter]);

  const name = value ? value.split("/").pop() : "";
  return (
    <div ref={ref} className={`drop ${over ? "over" : ""} ${value ? "filled" : ""}`}>
      <div className="drop-icon">{value ? <FileAudio size={19} /> : <Upload size={19} />}</div>
      <div className="drop-text">
        <b title={value}>{value ? name : label}</b>
        <span className="ellipsis" style={{ display: "block" }} title={value}>
          {value || hint}
        </span>
      </div>
      {value && <Button variant="ghost" size="sm" icon={X} onClick={() => onChange("")} />}
      <Button size="sm" onClick={async () => {
        const path = await pickFile(filter);
        if (path) onChange(path);
      }}>
        {value ? "更换" : "选择文件"}
      </Button>
    </div>
  );
}

// ---------------- 状态 ----------------

export function Badge({ tone, children, title }: {
  tone?: "accent" | "success" | "warning" | "danger";
  children: ReactNode;
  title?: string;
}) {
  return <span className={`badge ${tone ?? ""}`} title={title}>{children}</span>;
}

const AVATAR_COLORS = ["#5856D6", "#FF9500", "#34C759", "#FF2D55", "#32ADE6", "#AF52DE",
  "#F5A30A", "#A2845E"];

export function avatarColor(i: number) {
  return AVATAR_COLORS[((i % AVATAR_COLORS.length) + AVATAR_COLORS.length) % AVATAR_COLORS.length];
}

export function hashIndex(s: string) {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return Math.abs(h);
}

export function Avatar({ name, index, size }: { name: string; index?: number; size?: "sm" }) {
  const color = avatarColor(index ?? hashIndex(name));
  return (
    <span className={`avatar ${size ?? ""}`} style={{ background: color }}>
      {Array.from(name.trim())[0] ?? "?"}
    </span>
  );
}

const STATUS: Record<Task["status"], { label: string; icon: LucideIcon }> = {
  queued: { label: "排队中", icon: Clock },
  running: { label: "进行中", icon: LoaderCircle },
  succeeded: { label: "完成", icon: CircleCheck },
  failed: { label: "失败", icon: CircleX },
  cancelled: { label: "已取消", icon: Ban },
};

export function StatusLabel({ status }: { status: Task["status"] }) {
  const s = STATUS[status];
  const Icon = s.icon;
  return (
    <span className={`status ${status}`}>
      <Icon size={14} className={status === "running" ? "spin" : ""} />
      {s.label}
    </span>
  );
}

export function TaskProgress({ task, error, compact }: {
  task: Task | null;
  error?: string | null;
  /** 任务中心里已经单独显示了状态，这里只显示进度条或错误 */
  compact?: boolean;
}) {
  const engine = useEngine();
  if (error) return <TaskError text={error} />;
  if (!task) return null;
  if (task.status === "failed") return <TaskError text={task.error || task.message || "失败"} />;
  if (compact && task.status === "cancelled") return null;
  const active = task.status === "queued" || task.status === "running";
  const pct = Math.round(task.progress * 100);
  // 状态文字已经显示了，就不再重复同样的说明
  const msg = task.message && task.message !== STATUS[task.status].label ? task.message : "";
  return (
    <div className={`task ${compact ? "compact" : ""}`}>
      <div className="task-top">
        {!compact && <StatusLabel status={task.status} />}
        <span className="msg ellipsis">{msg}</span>
        {active && task.progress > 0 && <span className="small faint">{pct}%</span>}
        {active && (
          <Button variant="ghost" size="sm" onClick={() => engine.post(`/tasks/${task.id}/cancel`)}>
            取消
          </Button>
        )}
      </div>
      {active && (
        <div className={`bar ${task.progress === 0 ? "indeterminate" : ""}`}>
          <div style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

function TaskError({ text }: { text: string }) {
  return (
    <div className="task-error">
      <CircleX size={15} />
      <span>{text}</span>
    </div>
  );
}

export function Warnings({ items }: { items?: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <Callout>
      <ul style={{ margin: 0, paddingLeft: 16 }}>
        {items.map((w) => <li key={w}>{w}</li>)}
      </ul>
    </Callout>
  );
}

// ---------------- 弹窗 ----------------

export function Modal({ title, onClose, children, footer }: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog">
        <div className="modal-head">
          <h2>{title}</h2>
          <Button variant="ghost" size="sm" icon={X} onClick={onClose} />
        </div>
        {children}
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

// ---------------- 模型 ----------------

const COMMERCIAL_LABEL = { yes: "可商用", restricted: "有条件商用", no: "非商用" } as const;
const COMMERCIAL_TONE = { yes: "success", restricted: "warning", no: "danger" } as const;

export function LicenseBadge({ model }: { model: Pick<ModelInfo, "commercial" | "license"> }) {
  return (
    <Badge tone={COMMERCIAL_TONE[model.commercial]} title={model.license}>
      {COMMERCIAL_LABEL[model.commercial]}
    </Badge>
  );
}

/** 模型的简短说明：本地模型显示内存和许可，在线 API 显示是否已填 Key。 */
export function modelMeta(m: ModelInfo): string {
  if (m.worker === "cloud") {
    return m.worker_installed ? "在线 API" : "在线 API · 未填写 API Key";
  }
  const parts = [`约 ${m.memory_gb} GB`, COMMERCIAL_LABEL[m.commercial]];
  if (!m.fits) parts.push("本机内存不够");
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

/** 某类任务可选的模型（只列当前版本允许使用的），本地和在线 API 都在里面。
 * 留空表示用默认模型；在设置里选默认模型时，留空表示用按这台机器推荐的模型。 */
export function ModelSelect({ models, kinds, value, onChange, empty = "default" }: {
  models: ModelInfo[] | null;
  kinds: string[];
  value: string;
  onChange: (id: string) => void;
  empty?: "default" | "recommended";
}) {
  const options = (models ?? []).filter((m) => kinds.includes(m.kind) && m.allowed);
  const fallback = options.find((m) => (empty === "recommended" ? m.recommended : m.default));
  const local = options.filter((m) => m.worker !== "cloud");
  const cloud = options.filter((m) => m.worker === "cloud");
  const opt = (m: ModelInfo) => (
    <option key={m.id} value={m.id}>
      {m.name}{m.recommended ? "（推荐）" : ""} · {modelMeta(m)}
    </option>
  );
  const emptyLabel = empty === "recommended" ? "推荐" : "默认";
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{emptyLabel}{fallback ? `（${fallback.name}）` : ""}</option>
      {local.length > 0 && <optgroup label="本地模型">{local.map(opt)}</optgroup>}
      {cloud.length > 0 && <optgroup label="在线 API">{cloud.map(opt)}</optgroup>}
    </select>
  );
}

/** 选中的本地模型放不进这台机器的内存时提醒一下。 */
export function FitWarning({ model }: { model?: ModelInfo }) {
  if (!model || model.fits) return null;
  return (
    <Callout>
      这台 Mac 留给模型的内存不够运行 {model.name}（约 {model.memory_gb} GB），可能会失败。
      可以换成更小的模型，或者在设置里连接在线服务。
    </Callout>
  );
}
