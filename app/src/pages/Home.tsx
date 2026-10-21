// 首页：所有应用、最近任务、本机状态
import { ArrowRight, Cpu, HardDriveDownload, ListChecks } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge, Button, Card, Empty, StatusLabel, Tile } from "../components";
import { KIND_SHORT, RecommendedDownloads, formatBytes, useMissingRecommended } from "../downloads";
import { useEngineState, useResource, type ModelInfo, type SystemInfo } from "../engine";
import { APPS, TASK_KINDS, useNavigate, type PageId } from "../nav";

const SECTIONS: { title: string; apps: PageId[] }[] = [
  { title: "创作", apps: ["audiobook", "podcast", "music", "sfx"] },
  { title: "声音、听写与视频", apps: ["voices", "edit", "meeting", "interpret", "dubbing"] },
];

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 18) return "下午好";
  return "晚上好";
}

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function Home() {
  const go = useNavigate();
  const { tasks } = useEngineState();
  const system = useResource<SystemInfo>("/system");
  const recent = useMemo(
    () => Object.values(tasks).sort((a, b) => b.created_at - a.created_at).slice(0, 6),
    [tasks],
  );
  const s = system.data;
  const models = useResource<ModelInfo[]>("/catalog");
  const { items } = useMissingRecommended(models.data);
  const essential = items.filter((i) => i.essential);
  const [picking, setPicking] = useState(false);
  const size = essential.reduce(
    (n, i) => n + (i.status.remote_bytes ?? i.model.memory_gb * 1024 ** 3), 0);

  return (
    <div className="page">
      <div className="hero">
        <h1>{greeting()}，想做点什么？</h1>
        <p>所有声音都在这台 Mac 上生成。也可以在设置里接入在线服务。</p>
      </div>

      {essential.length > 0 && (
        <div className="banner">
          <span className="drop-icon"><HardDriveDownload size={18} /></span>
          <div className="grow">
            <b>先把常用模型下载好</b>
            <p>
              {`按这台 Mac 推荐的${essential.map((i) => KIND_SHORT[i.kind]).join("、")}模型还没下载`}
              {`（约 ${formatBytes(size)}）。提前下载好，用的时候就不用等；不下载也行，第一次用到时会自动下载。`}
            </p>
          </div>
          <Button variant="primary" onClick={() => setPicking(true)}>下载推荐模型</Button>
        </div>
      )}
      {picking && <RecommendedDownloads models={models.data} onClose={() => setPicking(false)} />}

      {SECTIONS.map((section) => (
        <div key={section.title}>
          <div className="section-title">{section.title}</div>
          <div className="app-grid">
            {section.apps.map((id) => {
              const meta = APPS[id];
              return (
                <button key={id} className="app-card" onClick={() => go(id)}>
                  <Tile app={id} size="md" />
                  <div>
                    <b>
                      {meta.label}
                      {meta.planned && <Badge>开发中</Badge>}
                    </b>
                    <p>{meta.desc}</p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ))}

      <div className="cols-main">
        <Card
          title="最近任务"
          actions={recent.length > 0 && (
            <button className="btn ghost sm" onClick={() => go("tasks")}>
              全部 <ArrowRight size={14} />
            </button>
          )}
        >
          {recent.length === 0 ? (
            <Empty icon={ListChecks} title="还没有任务">从上面任意一个应用开始。</Empty>
          ) : (
            <div className="list">
              {recent.map((t) => {
                const kind = TASK_KINDS[t.kind];
                return (
                  <div key={t.id} className="row-item" onClick={() => kind && go(kind.app)}>
                    <Tile app={kind?.app ?? "tasks"} size="md" />
                    <div className="grow">
                      <div className="title">{kind?.label ?? t.kind}</div>
                      <div className="meta ellipsis">
                        {t.status === "failed" ? t.error : t.message || ago(t.created_at)}
                      </div>
                    </div>
                    <StatusLabel status={t.status} />
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="这台 Mac" actions={
          <button className="btn ghost sm" onClick={() => go("models")}>
            模型管理 <ArrowRight size={14} />
          </button>
        }>
          {s ? (
            <>
              <div className="row-item" style={{ paddingTop: 0 }}>
                <span className="drop-icon"><Cpu size={18} /></span>
                <div className="grow">
                  <div className="title">{s.chip === "unknown" ? "Apple Silicon" : s.chip}</div>
                  <div className="meta">
                    内存 {s.memory_total_gb} GB · 模型可用 {s.memory_budget_gb} GB
                  </div>
                </div>
              </div>
              <div className="machine-head small" style={{ marginTop: 6 }}>
                <span className="muted">模型占用</span>
                <span>{s.scheduler.used_gb.toFixed(1)} GB</span>
              </div>
              <div className="gauge">
                <div style={{ width: `${Math.min(100, (s.scheduler.used_gb / s.memory_budget_gb) * 100)}%` }} />
              </div>
              <div className="divider" />
              <div className="kv">
                <div><span>版本</span><b>{s.edition === "commercial" ? "商用版" : "个人版"}</b></div>
                <div><span>默认 LLM</span><b>{s.llm_default}</b></div>
                <div><span>ffmpeg</span><b>{s.ffmpeg ? "已就绪" : "未找到"}</b></div>
              </div>
            </>
          ) : (
            <p className="muted">读取中…</p>
          )}
        </Card>
      </div>
    </div>
  );
}
