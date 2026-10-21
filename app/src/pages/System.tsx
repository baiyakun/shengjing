// 系统页面：任务中心、模型管理、设置、关于与许可
import { useEffect, useMemo, useState } from "react";
import {
  Field, LicenseBadge, Page, Player, Section, TaskProgress,
} from "../components";
import {
  useEngine, useEngineState, useResource, type ModelInfo, type SystemInfo, type Task,
} from "../engine";

const KIND_LABEL: Record<string, string> = {
  tts: "朗读 / 克隆", dialogue: "多人对话", voice_design: "声音设计", edit: "录音编辑",
  sfx: "音效", asr: "转写", asr_stream: "实时转写", llm: "LLM", diarization: "说话人区分",
  diarization_stream: "实时说话人区分", alignment: "时间戳", separation: "人声分离",
  vad: "人声检测", music: "音乐",
};

export function Tasks() {
  const { tasks } = useEngineState();
  const list = useMemo(
    () => Object.values(tasks).sort((a, b) => b.created_at - a.created_at),
    [tasks],
  );
  return (
    <Page title="任务中心" subtitle="所有生成任务按顺序排队执行，一次只跑一个，避免内存不够。">
      <Section>
        {list.length === 0 && <p className="muted">还没有任务。</p>}
        {list.map((t) => (
          <TaskRow key={t.id} task={t} />
        ))}
      </Section>
    </Page>
  );
}

function TaskRow({ task }: { task: Task }) {
  const output: string | undefined = task.result?.output ?? task.result?.path;
  return (
    <div className="task-row">
      <div className="row between">
        <b>{task.kind}</b>
        <span className="small muted">{new Date(task.created_at * 1000).toLocaleString()}</span>
      </div>
      <TaskProgress task={task} />
      {task.status === "succeeded" && output && <Player path={output} />}
    </div>
  );
}

export function Models() {
  const models = useResource<ModelInfo[]>("/catalog");
  const system = useResource<SystemInfo>("/system");
  const groups = useMemo(() => {
    const out: Record<string, ModelInfo[]> = {};
    for (const m of models.data ?? []) (out[m.kind] ??= []).push(m);
    return out;
  }, [models.data]);
  return (
    <Page
      title="模型管理"
      subtitle={system.data
        ? `${system.data.chip} · 内存 ${system.data.memory_total_gb} GB，可给模型用 ${system.data.memory_budget_gb} GB · 当前占用约 ${system.data.scheduler.used_gb} GB`
        : undefined}
    >
      {system.data && (
        <Section title="模型进程">
          <table>
            <thead><tr><th>进程</th><th>状态</th><th>已加载</th></tr></thead>
            <tbody>
              {system.data.scheduler.workers.map((w) => (
                <tr key={w.name}>
                  <td>{w.name}</td>
                  <td>{!w.installed ? <span className="warn">{w.unavailable_reason || "未安装"}</span>
                    : w.running ? "运行中" : "空闲（未启动）"}</td>
                  <td className="small">{w.loaded.join("、") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
      {Object.entries(groups).map(([kind, list]) => (
        <Section key={kind} title={KIND_LABEL[kind] ?? kind}>
          <table>
            <thead>
              <tr><th>模型</th><th>许可证</th><th>内存</th><th>说明</th></tr>
            </thead>
            <tbody>
              {list.map((m) => (
                <tr key={m.id} className={m.allowed ? "" : "disabled"}>
                  <td>
                    <a href={m.url} target="_blank" rel="noreferrer">{m.name}</a>
                    {m.default && <span className="badge">默认</span>}
                  </td>
                  <td>
                    <LicenseBadge model={m} /> <span className="small">{m.license}</span>
                    {m.license_note && <div className="small muted">{m.license_note}</div>}
                  </td>
                  <td className="small">
                    {m.memory_gb} GB{m.measured ? "（实测）" : "（估算）"}
                  </td>
                  <td className="small">
                    {m.notes}
                    {m.needs_conversion && <div className="warn">需要用 mlx-speech 脚本自行转换权重</div>}
                    {!m.worker_installed && <div className="warn">所在进程未安装</div>}
                    {!m.allowed && <div className="warn">商用版不可用</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      ))}
    </Page>
  );
}

interface SettingsShape {
  edition: "personal" | "commercial";
  llm: { provider: "local" | "cloud"; local_model: string | null; cloud_base_url: string;
    cloud_model: string; temperature: number };
  hf_endpoint: string;
  idle_timeout_s: number;
  qa_enabled: boolean;
  qa_max_retries: number;
  export_loudness_lufs: number;
}

export function Settings() {
  const engine = useEngine();
  const remote = useResource<{ settings: SettingsShape; secrets: Record<string, boolean> }>("/settings");
  const models = useResource<ModelInfo[]>("/catalog");
  const [s, setS] = useState<SettingsShape | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [hfToken, setHfToken] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (remote.data) setS(remote.data.settings);
  }, [remote.data]);
  if (!s) return <Page title="设置"><p className="muted">加载中…</p></Page>;

  const save = async () => {
    try {
      await engine.put("/settings", s);
      if (apiKey) await engine.put("/secrets/llm_api_key", { value: apiKey });
      if (hfToken) await engine.put("/secrets/hf_token", { value: hfToken });
      setApiKey("");
      setHfToken("");
      setMsg("已保存。模型下载源的修改在重启引擎后生效。");
      remote.reload();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };
  const llms = (models.data ?? []).filter((m) => m.kind === "llm");

  return (
    <Page title="设置">
      <Section title="版本">
        <label className="check">
          <input type="radio" checked={s.edition === "personal"}
            onChange={() => setS({ ...s, edition: "personal" })} />
          个人版：可以使用所有模型，包括非商用许可的模型
        </label>
        <label className="check">
          <input type="radio" checked={s.edition === "commercial"}
            onChange={() => setS({ ...s, edition: "commercial" })} />
          商用版：自动排除非商用许可的模型（如 Fish S2 Pro、Breeze TTS 2）
        </label>
      </Section>

      <Section title="LLM">
        <label className="check">
          <input type="radio" checked={s.llm.provider === "local"}
            onChange={() => setS({ ...s, llm: { ...s.llm, provider: "local" } })} />
          本地模型（mlx-lm，完全离线）
        </label>
        <label className="check">
          <input type="radio" checked={s.llm.provider === "cloud"}
            onChange={() => setS({ ...s, llm: { ...s.llm, provider: "cloud" } })} />
          云端接口（任意 OpenAI 兼容服务）
        </label>
        {s.llm.provider === "local" ? (
          <Field label="本地模型">
            <select value={s.llm.local_model ?? ""}
              onChange={(e) => setS({ ...s, llm: { ...s.llm, local_model: e.target.value || null } })}>
              <option value="">按内存自动选择</option>
              {llms.map((m) => (
                <option key={m.id} value={m.id}>{m.name} · 约 {m.memory_gb} GB</option>
              ))}
            </select>
          </Field>
        ) : (
          <div className="grid">
            <Field label="接口地址" hint="例如 https://api.deepseek.com/v1">
              <input value={s.llm.cloud_base_url}
                onChange={(e) => setS({ ...s, llm: { ...s.llm, cloud_base_url: e.target.value } })} />
            </Field>
            <Field label="模型名">
              <input value={s.llm.cloud_model}
                onChange={(e) => setS({ ...s, llm: { ...s.llm, cloud_model: e.target.value } })} />
            </Field>
            <Field label="API Key"
              hint={remote.data?.secrets.llm_api_key ? "已保存在系统钥匙串，留空不修改" : "保存在系统钥匙串"}>
              <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            </Field>
          </div>
        )}
      </Section>

      <Section title="模型下载">
        <Field label="下载源" hint="国内访问 Hugging Face 不稳定时可以填镜像站，例如 https://hf-mirror.com">
          <input value={s.hf_endpoint} onChange={(e) => setS({ ...s, hf_endpoint: e.target.value })} />
        </Field>
        <Field label="Hugging Face 访问令牌"
          hint={remote.data?.secrets.hf_token ? "已保存，留空不修改" : "说话人区分模型需要先在其页面同意使用条款，并填写令牌"}>
          <input type="password" value={hfToken} onChange={(e) => setHfToken(e.target.value)} />
        </Field>
      </Section>

      <Section title="生成和导出">
        <label className="check">
          <input type="checkbox" checked={s.qa_enabled}
            onChange={(e) => setS({ ...s, qa_enabled: e.target.checked })} />
          默认开启质检回听
        </label>
        <div className="grid">
          <Field label="质检不过时最多重做几次">
            <input type="number" min={0} max={5} value={s.qa_max_retries}
              onChange={(e) => setS({ ...s, qa_max_retries: Number(e.target.value) })} />
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
      </Section>
      <div className="row">
        <button className="primary" onClick={save}>保存设置</button>
        {msg && <span className="small">{msg}</span>}
      </div>
    </Page>
  );
}

const FRAMEWORKS = [
  ["Tauri", "MIT / Apache-2.0"], ["React", "MIT"], ["Vite", "MIT"], ["MLX", "MIT"],
  ["mlx-speech", "MIT"], ["mlx-lm", "MIT"], ["FastAPI", "MIT"], ["Uvicorn", "BSD-3-Clause"],
  ["PyTorch", "BSD-3-Clause"], ["pyannote.audio", "MIT"], ["Demucs", "MIT"],
  ["ACE-Step 1.5", "MIT"], ["FFmpeg（LGPL 构建）", "LGPL-2.1+"],
];

export function About() {
  const models = useResource<ModelInfo[]>("/catalog");
  const fish = (models.data ?? []).some((m) => m.license.startsWith("Fish Audio"));
  return (
    <Page title="关于与许可" subtitle="声境使用的开源框架和模型，以及它们的许可要求。">
      <Section title="必要声明">
        <ul>
          {fish && (
            <li>
              Built with Fish Audio。Fish S2 Pro 和 Drama 3 依据 Fish Audio Research License
              使用（Copyright © 39 AI, INC.），仅限研究和非商用。
            </li>
          )}
          <li>pyannote speaker-diarization-community-1、NVIDIA Streaming Sortformer v2 依据 CC-BY-4.0 使用。</li>
          <li>生成的语音、音乐和音效均为 AI 生成内容，发布时请按规定标注。</li>
        </ul>
      </Section>
      <Section title="框架">
        <table>
          <tbody>
            {FRAMEWORKS.map(([name, lic]) => (
              <tr key={name}><td>{name}</td><td className="small">{lic}</td></tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="模型">
        <table>
          <tbody>
            {(models.data ?? []).map((m) => (
              <tr key={m.id}>
                <td><a href={m.url} target="_blank" rel="noreferrer">{m.name}</a></td>
                <td><LicenseBadge model={m} /> <span className="small">{m.license}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </Page>
  );
}
