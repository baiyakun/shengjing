// 模型下载的界面部件：单个模型的下载按钮和进度、下载推荐模型的弹窗、下载源选择
import { CircleCheck, Download, HardDriveDownload, Trash2, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Button, Modal } from "./components";
import {
  useDownloads, useResource, type DownloadStatus, type ModelInfo, type SystemInfo,
} from "./engine";

export function formatBytes(n: number | null | undefined): string {
  if (!n) return "0 MB";
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(n / 1024 ** 2))} MB`;
}

/** 下载大小：查到真实大小就用真实的，否则用内存估算粗略代替。 */
export function downloadSize(st: DownloadStatus | undefined, model?: ModelInfo): string {
  if (st?.remote_bytes) return formatBytes(st.remote_bytes);
  return model ? `约 ${model.memory_gb} GB` : "";
}

const ACTIVE = new Set(["queued", "running"]);

/** 一个模型的下载状态和操作。 */
export function DownloadControl({ model, status, actions, compact }: {
  model: ModelInfo;
  status: DownloadStatus | undefined;
  actions: ReturnType<typeof useDownloads>;
  compact?: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  if (!status) return null;
  const job = status.job;
  const run = (fn: () => Promise<unknown>) => {
    setError(null);
    fn().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  };

  if (job && ACTIVE.has(job.state)) {
    const pct = Math.round(job.fraction * 100);
    return (
      <div className={`dl ${compact ? "compact" : ""}`}>
        <div className="dl-top">
          <span className="dl-label">
            {job.state === "queued" ? "排队中" : job.total_bytes
              ? `${pct}% · ${formatBytes(job.done_bytes)} / ${formatBytes(job.total_bytes)}`
              : job.message}
          </span>
          <Button variant="ghost" size="sm" icon={X} title="取消下载"
            onClick={() => run(() => actions.cancel(model.id))} />
        </div>
        <div className={`bar ${job.state === "queued" || !job.total_bytes ? "indeterminate" : ""}`}>
          <div style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  if (status.state === "ready") {
    return (
      <div className={`dl ${compact ? "compact" : ""}`}>
        <div className="dl-top">
          <span className="dl-label ok"><CircleCheck size={13} />已下载 · {formatBytes(status.bytes)}</span>
          {!compact && (confirm ? (
            <span className="dl-confirm">
              <Button variant="danger" size="sm" onClick={() => {
                setConfirm(false);
                run(() => actions.remove(model.id));
              }}>删除</Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirm(false)}>取消</Button>
            </span>
          ) : (
            <Button variant="ghost" size="sm" icon={Trash2} title="删除下载的文件，释放磁盘空间"
              onClick={() => setConfirm(true)} />
          ))}
        </div>
        {error && <div className="dl-error">{error}</div>}
      </div>
    );
  }

  if (status.state === "missing" || status.state === "partial") {
    const failed = job?.state === "failed";
    return (
      <div className={`dl ${compact ? "compact" : ""}`}>
        <Button size="sm" icon={Download} onClick={() => run(() => actions.start(model.id))}>
          {failed ? "重试" : status.state === "partial" ? "继续下载" : "下载"} · {downloadSize(status, model)}
        </Button>
        {failed && <div className="dl-error"><TriangleAlert size={12} />{job.error}</div>}
        {!failed && status.gated && !compact && (
          <div className="dl-note">需要先在 Hugging Face 同意使用条款，并填写访问令牌</div>
        )}
        {error && <div className="dl-error">{error}</div>}
      </div>
    );
  }

  return compact ? null : <div className="dl"><div className="dl-note">{status.note}</div></div>;
}

// 推荐模型里默认勾选的：几乎每个应用都要用到
const ESSENTIAL = ["tts", "asr", "llm", "sfx"];
export const KIND_SHORT: Record<string, string> = {
  tts: "朗读", asr: "转写", llm: "LLM", sfx: "音效", dialogue: "多人对话", music: "音乐",
  edit: "录音后期", diarization: "区分发言人",
};
const KIND_LABEL: Record<string, string> = {
  tts: "朗读 / 克隆", asr: "语音转文字", llm: "LLM（写剧本、纪要、歌词）", sfx: "音效",
  dialogue: "多人对话", music: "音乐", edit: "录音后期", diarization: "区分发言人",
};

/** 按这台 Mac 推荐、还没下载的本地模型。 */
export function useMissingRecommended(models: ModelInfo[] | null) {
  const system = useResource<SystemInfo>("/system");
  const actions = useDownloads();
  const items = useMemo(() => {
    const recs = system.data?.recommendations ?? {};
    const out: { kind: string; model: ModelInfo; status: DownloadStatus; essential: boolean }[] = [];
    const seen = new Set<string>();
    for (const kind of Object.keys(KIND_LABEL)) {
      const id = recs[kind]?.model;
      const model = models?.find((m) => m.id === id);
      const status = id ? actions.status(id) : undefined;
      if (!id || !model || !status || seen.has(id)) continue;
      // 在线服务、放不下、所在的扩展包还没装的不推荐下载
      if (model.worker === "cloud" || !model.fits || !model.worker_installed) continue;
      if (status.state !== "missing" && status.state !== "partial") continue;
      seen.add(id);
      out.push({ kind, model, status, essential: ESSENTIAL.includes(kind) });
    }
    return out;
  }, [system.data, models, actions]);
  return { items, actions, system: system.data };
}

export function RecommendedDownloads({ models, onClose }: {
  models: ModelInfo[] | null;
  onClose: () => void;
}) {
  const { items, actions } = useMissingRecommended(models);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const chosen = picked ?? new Set(items.filter((i) => i.essential).map((i) => i.model.id));
  const size = (i: (typeof items)[number]) => i.status.remote_bytes ?? i.model.memory_gb * 1024 ** 3;
  const total = items.filter((i) => chosen.has(i.model.id)).reduce((n, i) => n + size(i), 0);
  const free = actions.overview?.free_bytes ?? 0;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      for (const id of chosen) await actions.start(id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="下载推荐模型" onClose={onClose} footer={
      <>
        <span className="small muted grow">
          共 {formatBytes(total)}{free ? ` · 磁盘剩余 ${formatBytes(free)}` : ""}
        </span>
        <Button onClick={onClose}>取消</Button>
        <Button variant="primary" icon={HardDriveDownload} loading={busy}
          disabled={chosen.size === 0 || (free > 0 && total > free)} onClick={start}>
          开始下载
        </Button>
      </>
    }>
      {items.length === 0 ? (
        <p className="muted">这台 Mac 推荐的模型都已经下载好了。</p>
      ) : (
        <>
          <p className="small muted" style={{ marginBottom: 12 }}>
            按这台 Mac 的内存推荐。下载完成后可以离线使用；没下载的模型也会在第一次用到时自动下载。
          </p>
          <div className="pick-list">
            {items.map((i) => (
              <label key={i.model.id} className="pick">
                <input type="checkbox" checked={chosen.has(i.model.id)} onChange={(e) => {
                  const next = new Set(chosen);
                  if (e.target.checked) next.add(i.model.id);
                  else next.delete(i.model.id);
                  setPicked(next);
                }} />
                <div className="grow">
                  <div className="pick-title">{KIND_LABEL[i.kind]} · {i.model.name}</div>
                  <div className="small muted">
                    {i.status.state === "partial" ? "下载了一部分，会接着下载" : i.model.notes}
                  </div>
                </div>
                <span className="small">{downloadSize(i.status, i.model)}</span>
              </label>
            ))}
          </div>
          {free > 0 && total > free && (
            <p className="dl-error" style={{ marginTop: 10 }}>磁盘空间不够，请少选几个</p>
          )}
          {error && <p className="dl-error" style={{ marginTop: 10 }}>{error}</p>}
        </>
      )}
    </Modal>
  );
}

export const HF_OFFICIAL = "";
export const HF_MIRROR = "https://hf-mirror.com";

/** 正在下载的模型（侧边栏用）。 */
export function activeDownloads(downloads: Record<string, { state: string; fraction: number }>) {
  // 正在下载的排前面，其次是排队中的
  return Object.entries(downloads).filter(([, j]) => ACTIVE.has(j.state))
    .sort(([, a], [, b]) => (a.state === "running" ? 0 : 1) - (b.state === "running" ? 0 : 1));
}
