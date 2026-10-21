// 系统页面：任务中心、模型管理、设置、关于与许可
import {
  Cloud, Cpu, ExternalLink, FolderOpen, HardDrive, HardDriveDownload, ListChecks, Save,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Badge, Button, Card, Empty, Field, LicenseBadge, Page, Player, Segmented, StatusLabel,
  TaskProgress, Tile, Toggle,
} from "../components";
import {
  inTauri, useEngine, useEngineState, useResource, type ModelInfo, type SystemInfo,
  type Task,
} from "../engine";
import {
  DownloadControl, HF_MIRROR, HF_OFFICIAL, RecommendedDownloads, formatBytes, useMissingRecommended,
} from "../downloads";
import { revealFile } from "../files";
import { TASK_KINDS } from "../nav";
import { DefaultModels, OnlineApis } from "./Providers";

const KIND_LABEL: Record<string, string> = {
  tts: "朗读 / 克隆", dialogue: "多人对话", voice_design: "声音设计", edit: "录音编辑",
  sfx: "音效", music: "音乐", asr: "转写", asr_stream: "实时转写", llm: "本地 LLM",
  diarization: "说话人区分", diarization_stream: "实时说话人区分", alignment: "时间戳",
  separation: "人声分离", vad: "人声检测",
};
const KIND_ORDER = Object.keys(KIND_LABEL);
const WORKER_LABEL: Record<string, string> = {
  speech: "语音进程", llm: "LLM 进程", analysis: "分析进程（扩展包）", music: "音乐进程（扩展包）",
};

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return new Date(ts * 1000).toLocaleString();
}

// ---------------- 任务中心 ----------------

type Filter = "all" | "active" | "succeeded" | "failed";

export function Tasks() {
  const { tasks } = useEngineState();
  const [filter, setFilter] = useState<Filter>("all");
  const list = useMemo(() => Object.values(tasks)
    .filter((t) => filter === "all"
      || (filter === "active" && (t.status === "queued" || t.status === "running"))
      || t.status === filter)
    .sort((a, b) => b.created_at - a.created_at), [tasks, filter]);
  return (
    <Page app="tasks" actions={
      <Segmented value={filter} onChange={setFilter} options={[
        { value: "all", label: "全部" }, { value: "active", label: "进行中" },
        { value: "succeeded", label: "完成" }, { value: "failed", label: "失败" },
      ]} />
    }>
      {list.length === 0 ? (
        <Card><Empty icon={ListChecks} title="没有任务">生成任务会在这里排队执行。</Empty></Card>
      ) : (
        <Card flush>
          <div className="list">{list.map((t) => <TaskRow key={t.id} task={t} />)}</div>
        </Card>
      )}
    </Page>
  );
}

function TaskRow({ task }: { task: Task }) {
  const kind = TASK_KINDS[task.kind];
  const output: string | undefined = task.result?.output ?? task.result?.path;
  const active = task.status === "queued" || task.status === "running";
  return (
    <div className="row-item" style={{ alignItems: "flex-start" }}>
      <Tile app={kind?.app ?? "tasks"} size="md" />
      <div className="grow">
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="title">{kind?.label ?? task.kind}</span>
          <span className="meta">{ago(task.created_at)}</span>
          <span style={{ flex: 1 }} />
          {!active && <StatusLabel status={task.status} />}
        </div>
        {(active || task.status === "failed") && <TaskProgress task={task} compact />}
        {task.status === "succeeded" && output && (
          <div style={{ marginTop: 10 }}><Player path={output} /></div>
        )}
      </div>
    </div>
  );
}

// ---------------- 模型管理 ----------------

export function Models() {
  const models = useResource<ModelInfo[]>("/catalog");
  const system = useResource<SystemInfo>("/system");
  const { items: missing, actions } = useMissingRecommended(models.data);
  const [scope, setScope] = useState<"all" | "local" | "ready" | "cloud">("all");
  const [picking, setPicking] = useState(false);
  const groups = useMemo(() => {
    const out: Record<string, ModelInfo[]> = {};
    for (const m of models.data ?? []) {
      if (scope === "local" && m.worker === "cloud") continue;
      if (scope === "cloud" && m.worker !== "cloud") continue;
      if (scope === "ready" && actions.status(m.id)?.state !== "ready") continue;
      (out[m.kind] ??= []).push(m);
    }
    return KIND_ORDER.filter((k) => out[k]).map((k) => [k, out[k]] as const);
  }, [models.data, scope, actions]);
  const s = system.data;
  const o = actions.overview;
  const readyCount = o ? Object.values(o.models).filter((m) => m.state === "ready").length : 0;
  const active = (models.data ?? []).filter((m) => {
    const job = actions.status(m.id)?.job;
    return job && (job.state === "queued" || job.state === "running");
  });

  return (
    <Page app="models" actions={
      <Segmented value={scope} onChange={setScope} options={[
        { value: "all", label: "全部" }, { value: "local", label: "本地", icon: HardDrive },
        { value: "ready", label: "已下载" }, { value: "cloud", label: "在线", icon: Cloud },
      ]} />
    }>
      {o && (
        <Card title="模型文件"
          sub={`已下载 ${readyCount} 个，占用 ${formatBytes(o.used_bytes)} · 磁盘剩余 ${formatBytes(o.free_bytes)}`}
          actions={
            <Button variant={missing.length ? "primary" : "default"} icon={HardDriveDownload}
              onClick={() => setPicking(true)}>
              下载推荐模型{missing.length ? `（${missing.length}）` : ""}
            </Button>
          }>
          <div className="kv">
            <div>
              <span>下载源</span>
              <b>{o.endpoint === HF_MIRROR ? "国内镜像 hf-mirror.com"
                : o.endpoint && o.endpoint !== HF_OFFICIAL ? o.endpoint : "Hugging Face 官方"}
                <span className="faint" style={{ fontWeight: 400 }}> · 在设置 → 下载与生成里更改</span></b>
            </div>
            <div>
              <span>存放位置</span>
              <b className="path-line">
                <span className="ellipsis" title={o.path}>{o.path}</span>
                {inTauri && (
                  <Button variant="ghost" size="sm" icon={FolderOpen} title="在访达中显示"
                    onClick={() => revealFile(o.path)} />
                )}
              </b>
            </div>
          </div>
          {active.length > 0 && (
            <>
              <div className="divider" />
              <div className="section-title">正在下载</div>
              {active.map((m) => (
                <div key={m.id} className="active-dl">
                  <span className="grow">{m.name}</span>
                  <DownloadControl model={m} status={actions.status(m.id)} actions={actions} />
                </div>
              ))}
            </>
          )}
          <p className="small muted" style={{ marginTop: 12 }}>
            没下载的模型会在第一次用到时自动下载，进度显示在任务里；提前下载好就不用等，也可以离线使用。
          </p>
        </Card>
      )}
      {picking && <RecommendedDownloads models={models.data} onClose={() => setPicking(false)} />}

      {s && (
        <div className="cols" style={{ marginBottom: 16, marginTop: 16 }}>
          <Card title="内存" sub={`${s.chip === "unknown" ? "Apple Silicon" : s.chip} · 共 ${s.memory_total_gb} GB`}>
            <div className="machine-head">
              <span className="muted">模型占用 / 可用</span>
              <span><b>{s.scheduler.used_gb.toFixed(1)}</b> / {s.memory_budget_gb} GB</span>
            </div>
            <div className="gauge">
              <div style={{ width: `${Math.min(100, (s.scheduler.used_gb / s.memory_budget_gb) * 100)}%` }} />
            </div>
            <p className="small muted" style={{ marginTop: 8 }}>
              超出可用内存时，会自动关闭最久没用的模型进程。
            </p>
            <div className="divider" />
            <div className="kv">
              <div><span>系统和其他软件预留</span><b>{(s.memory_total_gb - s.memory_budget_gb).toFixed(1)} GB</b></div>
              <div><span>默认 LLM</span><b>{s.llm_default}</b></div>
              <div><span>ffmpeg</span><b>{s.ffmpeg ? "已就绪" : "未找到"}</b></div>
            </div>
          </Card>
          <Card title="模型进程">
            <div className="list">
              {s.scheduler.workers.map((w) => (
                <div key={w.name} className="row-item" style={{ padding: "7px 0" }}>
                  <span className={`dot ${w.running ? "on" : ""}`} />
                  <div className="grow">
                    <div>{WORKER_LABEL[w.name] ?? w.name}</div>
                    <div className="meta ellipsis">
                      {!w.installed ? w.unavailable_reason || "未安装"
                        : w.running ? `已加载：${w.loaded.join("、") || "—"}` : "未启动，用到时自动启动"}
                    </div>
                  </div>
                  {!w.installed && <Badge tone="warning">未安装</Badge>}
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {groups.map(([kind, list]) => (
        <Card key={kind} flush title={KIND_LABEL[kind] ?? kind} sub={`${list.length} 个`}>
          <div style={{ marginTop: 12 }}>
            {list.map((m) => (
              <div key={m.id} className={`model-row ${m.allowed ? "" : "off"}`}>
                <div style={{ minWidth: 0 }}>
                  <div className="name">
                    <a href={m.url} target="_blank" rel="noreferrer">{m.name}</a>
                    {m.recommended && <Badge tone="accent" title="按这台 Mac 的内存和芯片推荐">推荐</Badge>}
                    {m.default && !m.recommended && <Badge tone="accent" title="在设置里选定的默认模型">默认</Badge>}
                    {m.worker === "cloud" ? <Badge><Cloud size={11} />在线</Badge>
                      : <Badge><HardDrive size={11} />本地</Badge>}
                    <LicenseBadge model={m} />
                    {!m.worker_installed && (
                      <Badge tone="warning">{m.worker === "cloud" ? "未填 API Key" : "扩展包未安装"}</Badge>
                    )}
                    {m.needs_conversion && <Badge tone="warning">需自行转换权重</Badge>}
                    {!m.fits && <Badge tone="danger">本机内存不够</Badge>}
                  </div>
                  <div className="desc">
                    {m.notes}
                    {m.license_note && (
                      <div className="faint">
                        {m.license_note.startsWith(m.license) ? m.license_note : `${m.license}：${m.license_note}`}
                      </div>
                    )}
                  </div>
                </div>
                <div className="mem">
                  {m.worker === "cloud" ? <><b>—</b>不占内存</> : (
                    <><b>{m.memory_gb} GB</b>{m.measured ? "实测内存" : "估算内存"}</>
                  )}
                </div>
                <div className="dl-cell">
                  {m.worker !== "cloud" && (
                    <DownloadControl model={m} status={actions.status(m.id)} actions={actions} />
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </Page>
  );
}

// ---------------- 设置 ----------------

interface SettingsShape {
  edition: "personal" | "commercial";
  model_overrides: Record<string, string>;
  llm: { provider: "local" | "cloud"; local_model: string | null; cloud_base_url: string;
    cloud_model: string; temperature: number };
  hf_endpoint: string;
  idle_timeout_s: number;
  qa_enabled: boolean;
  qa_max_retries: number;
  export_loudness_lufs: number;
}

type Tab = "defaults" | "online" | "llm" | "general" | "edition";

export function Settings() {
  const engine = useEngine();
  const remote = useResource<{ settings: SettingsShape; secrets: Record<string, boolean> }>("/settings");
  const models = useResource<ModelInfo[]>("/catalog");
  const system = useResource<SystemInfo>("/system");
  const [tab, setTab] = useState<Tab>("defaults");
  const [s, setS] = useState<SettingsShape | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [hfToken, setHfToken] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (remote.data) setS(remote.data.settings);
  }, [remote.data]);
  if (!s) return <Page app="settings"><p className="muted">读取中…</p></Page>;

  const save = async () => {
    try {
      await engine.put("/settings", s);
      if (apiKey) await engine.put("/secrets/llm_api_key", { value: apiKey });
      if (hfToken) await engine.put("/secrets/hf_token", { value: hfToken });
      setApiKey("");
      setHfToken("");
      setMsg("已保存");
      setTimeout(() => setMsg(null), 2500);
      remote.reload();
      models.reload();
      system.reload(); // 推荐会随设置变化（例如转写改用在线服务、关掉质检回听）
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };
  const llms = (models.data ?? []).filter((m) => m.kind === "llm");
  const llmRec = system.data?.recommendations.llm;
  const saveBar = (
    <div className="btn-row" style={{ marginTop: 4 }}>
      <Button variant="primary" icon={Save} onClick={save}>保存</Button>
      {msg && <span className="small muted">{msg}</span>}
    </div>
  );

  return (
    <Page app="settings" subtitle="模型、在线服务和生成选项" narrow>
      <div className="tabs">
        <Segmented value={tab} onChange={setTab} block options={[
          { value: "defaults", label: "默认模型" }, { value: "online", label: "在线 API" },
          { value: "llm", label: "LLM" }, { value: "general", label: "下载与生成" },
          { value: "edition", label: "版本" },
        ]} />
      </div>

      {tab === "defaults" && (
        <>
          <DefaultModels overrides={s.model_overrides} models={models.data} system={system.data}
            onChange={(next) => setS({ ...s, model_overrides: next })} />
          {saveBar}
        </>
      )}

      {tab === "online" && <OnlineApis />}

      {tab === "llm" && (
        <>
          <Card title="写剧本、纪要、歌词用的 LLM">
            <Field label="来源">
              <Segmented value={s.llm.provider} block
                onChange={(provider) => setS({ ...s, llm: { ...s.llm, provider } })}
                options={[
                  { value: "local", label: "本地模型（离线）", icon: HardDrive },
                  { value: "cloud", label: "云端接口", icon: Cloud },
                ]} />
            </Field>
            {s.llm.provider === "local" ? (
              <Field label="本地模型"
                hint={llmRec ? `推荐理由：${llmRec.reason}。通过 mlx-lm 在本机运行，第一次使用时下载`
                  : "通过 mlx-lm 在本机运行，第一次使用时下载"}>
                <select value={s.llm.local_model ?? ""}
                  onChange={(e) => setS({ ...s, llm: { ...s.llm, local_model: e.target.value || null } })}>
                  <option value="">
                    按这台 Mac 推荐{llmRec?.model ? `（${llms.find((m) => m.id === llmRec.model)?.name ?? llmRec.model}）` : ""}
                  </option>
                  {llms.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}{m.recommended ? "（推荐）" : ""} · 约 {m.memory_gb} GB{m.fits ? "" : " · 本机内存不够"}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <>
                <Field label="接口地址" hint="任意 OpenAI 兼容的对话接口，例如 https://api.deepseek.com/v1">
                  <input value={s.llm.cloud_base_url}
                    onChange={(e) => setS({ ...s, llm: { ...s.llm, cloud_base_url: e.target.value } })} />
                </Field>
                <div className="fields">
                  <Field label="模型名">
                    <input value={s.llm.cloud_model}
                      onChange={(e) => setS({ ...s, llm: { ...s.llm, cloud_model: e.target.value } })} />
                  </Field>
                  <Field label="API Key"
                    hint={remote.data?.secrets.llm_api_key ? "已保存在钥匙串，留空不修改" : "保存在系统钥匙串"}>
                    <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
                  </Field>
                </div>
              </>
            )}
          </Card>
          {saveBar}
        </>
      )}

      {tab === "general" && (
        <>
          <Card title="模型下载">
            <Field label="下载源" hint="国内访问 Hugging Face 慢或连不上时选镜像站。保存后马上生效">
              <Segmented block
                value={s.hf_endpoint === HF_OFFICIAL ? "official"
                  : s.hf_endpoint.replace(/\/$/, "") === HF_MIRROR ? "mirror" : "custom"}
                onChange={(v) => setS({ ...s, hf_endpoint: v === "official" ? HF_OFFICIAL
                  : v === "mirror" ? HF_MIRROR : (s.hf_endpoint || "https://") })}
                options={[
                  { value: "official", label: "Hugging Face 官方" },
                  { value: "mirror", label: "国内镜像 hf-mirror.com" },
                  { value: "custom", label: "自定义" },
                ]} />
            </Field>
            {s.hf_endpoint !== HF_OFFICIAL && s.hf_endpoint.replace(/\/$/, "") !== HF_MIRROR && (
              <Field label="自定义下载源地址">
                <input value={s.hf_endpoint} placeholder="https://"
                  onChange={(e) => setS({ ...s, hf_endpoint: e.target.value })} />
              </Field>
            )}
            <Field label="Hugging Face 访问令牌"
              hint={remote.data?.secrets.hf_token ? "已保存，留空不修改"
                : "本地说话人区分模型需要先在其页面同意使用条款，再填写令牌"}>
              <input type="password" value={hfToken} onChange={(e) => setHfToken(e.target.value)} />
            </Field>
          </Card>
          <Card title="生成和导出">
            <Toggle checked={s.qa_enabled} onChange={(qa_enabled) => setS({ ...s, qa_enabled })}
              label="默认开启质检回听" hint="生成后转写回文字比对，不合格的句子自动重做" />
            <div className="divider" />
            <div className="fields">
              <Field label="质检不过时最多重做">
                <Segmented value={s.qa_max_retries} block
                  onChange={(qa_max_retries) => setS({ ...s, qa_max_retries })}
                  options={[0, 1, 2, 3].map((n) => ({ value: n, label: `${n} 次` }))} />
              </Field>
              <Field label="导出响度（LUFS）" hint="播客常用 -16，有声书常用 -18 到 -20">
                <input type="number" step={1} value={s.export_loudness_lufs}
                  onChange={(e) => setS({ ...s, export_loudness_lufs: Number(e.target.value) })} />
              </Field>
              <Field label="模型进程空闲多久后关闭（秒）">
                <input type="number" min={60} value={s.idle_timeout_s}
                  onChange={(e) => setS({ ...s, idle_timeout_s: Number(e.target.value) })} />
              </Field>
            </div>
          </Card>
          {saveBar}
        </>
      )}

      {tab === "edition" && (
        <>
          <Card title="版本">
            <div className="choices" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <button className={`choice ${s.edition === "personal" ? "on" : ""}`}
                onClick={() => setS({ ...s, edition: "personal" })}>
                <b>个人版</b>
                <small>可以使用所有模型，包括 Fish S2 Pro、Breeze TTS 2 等非商用许可的模型。</small>
              </button>
              <button className={`choice ${s.edition === "commercial" ? "on" : ""}`}
                onClick={() => setS({ ...s, edition: "commercial" })}>
                <b>商用版</b>
                <small>自动排除非商用许可的模型，并切换到可商用的默认模型。</small>
              </button>
            </div>
          </Card>
          {saveBar}
        </>
      )}
    </Page>
  );
}

// ---------------- 关于 ----------------

const FRAMEWORKS = [
  ["Tauri", "MIT / Apache-2.0"], ["React", "MIT"], ["Vite", "MIT"], ["Lucide 图标", "ISC"],
  ["MLX", "MIT"], ["mlx-speech", "MIT"], ["mlx-lm", "MIT"], ["FastAPI", "MIT"],
  ["Uvicorn", "BSD-3-Clause"], ["huggingface_hub", "Apache-2.0"], ["websockets", "BSD-3-Clause"], ["msgpack", "Apache-2.0"],
  ["PyTorch", "BSD-3-Clause"], ["pyannote.audio", "MIT"], ["Demucs", "MIT"],
  ["ACE-Step 1.5", "MIT"], ["FFmpeg（LGPL 构建）", "LGPL-2.1+"],
];

export function About() {
  const models = useResource<ModelInfo[]>("/catalog");
  const local = (models.data ?? []).filter((m) => m.worker !== "cloud");
  const fish = local.some((m) => m.license.startsWith("Fish Audio"));
  return (
    <Page app="about" subtitle="声境使用的开源框架和模型，以及它们的许可要求" narrow>
      <Card title="声明">
        <ul className="plain-list">
          <li>声境的代码使用 MIT 许可证（Copyright © 2026 baiyakun），仅供个人研究使用。</li>
          {fish && (
            <li>Built with Fish Audio。Fish S2 Pro 和 Drama 3 依据 Fish Audio Research License 使用
              （Copyright © 39 AI, INC.），仅限研究和非商用。</li>
          )}
          <li>pyannote speaker-diarization-community-1、NVIDIA Streaming Sortformer v2 依据 CC-BY-4.0 使用。</li>
          <li>生成的语音、音乐和音效均为 AI 生成内容，发布时请按规定标注。</li>
        </ul>
      </Card>
      <Card flush title="框架">
        <div style={{ marginTop: 8 }}>
          {FRAMEWORKS.map(([name, lic]) => (
            <div key={name} className="model-row" style={{ padding: "9px 18px" }}>
              <span>{name}</span><span className="mem">{lic}</span>
            </div>
          ))}
        </div>
      </Card>
      <Card flush title="本地模型">
        <div style={{ marginTop: 8 }}>
          {local.map((m) => (
            <div key={m.id} className="model-row" style={{ padding: "9px 18px" }}>
              <span className="name">
                <a href={m.url} target="_blank" rel="noreferrer">{m.name} <ExternalLink size={11} /></a>
                <LicenseBadge model={m} />
              </span>
              <span className="mem" style={{ whiteSpace: "nowrap" }}>{m.license}</span>
            </div>
          ))}
        </div>
      </Card>
      <p className="small faint" style={{ textAlign: "center" }}>
        <Cpu size={12} style={{ verticalAlign: -2 }} /> 声境 0.1.0
      </p>
    </Page>
  );
}
