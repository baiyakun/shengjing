// 有声书 / 播客：原文 → LLM 剧本 → 选角 → 编辑台词 → 配音（自动质检）→ 混音导出
import { FilePlus2, RotateCcw, Save, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Avatar, Badge, Button, Card, Empty, Field, ModelSelect, Page, Player, Segmented, TaskProgress,
  Toggle, Warnings,
} from "../components";
import { useEngine, useResource, useTask, type ModelInfo, type Voice } from "../engine";

interface CastMember { id: string; name: string; description: string; voice_id: string | null }
interface Line {
  id: string; speaker: string; text: string; delivery: string; tags: string[];
  duration: number | null; pause_after: number;
}
interface Script {
  title: string; language: "zh" | "en"; cast: CastMember[]; lines: Line[]; cues: unknown[];
}
interface Project {
  id: string; kind: string; title: string; data: { script: Script };
  renders: { line_id: string; version: number; selected: boolean; qa: { passed?: boolean } }[];
}
interface ProjectSummary { id: string; title: string; updated_at: number }

const COPY = {
  audiobook: {
    placeholder: "粘贴一章小说。LLM 会把叙述交给旁白、对白交给各个角色，并标出语气、音效和配乐。",
    hint: "建议先用几段试效果，再处理整章。",
  },
  podcast: {
    placeholder: "粘贴文章、资料或网页正文。LLM 会改写成两位主持人有来有回的对谈。",
    hint: "资料越具体，对谈内容越扎实。",
  },
} as const;

const FORMATS = [
  { value: "mp3", label: "MP3" },
  { value: "m4a", label: "M4A" },
  { value: "wav", label: "WAV" },
  { value: "flac", label: "FLAC" },
];

export function Studio({ style }: { style: "audiobook" | "podcast" }) {
  const engine = useEngine();
  const copy = COPY[style];
  const projects = useResource<ProjectSummary[]>(`/projects?kind=${style}`);
  const voices = useResource<Voice[]>("/voices");
  const models = useResource<ModelInfo[]>("/catalog");
  const [projectId, setProjectId] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);

  const [source, setSource] = useState("");
  const [title, setTitle] = useState("");
  const scriptTask = useTask();
  const renderTask = useTask();

  const [model, setModel] = useState("");
  const [dialogue, setDialogue] = useState(false);
  const [qa, setQa] = useState(true);
  const [format, setFormat] = useState("mp3");

  const loadProject = async (id: string) => {
    setProjectId(id);
    setDirty(false);
    setShowSource(false);
    setProject(id ? await engine.get<Project>(`/projects/${id}`) : null);
  };

  // 剧本生成完成后自动打开新项目
  useEffect(() => {
    const t = scriptTask.task;
    if (t?.status === "succeeded" && t.result?.project_id && t.result.project_id !== projectId) {
      projects.reload();
      loadProject(t.result.project_id);
    }
  }, [scriptTask.task?.status]);

  // 配音完成后刷新（拿到每句的版本信息）
  useEffect(() => {
    if (renderTask.task?.status === "succeeded" && projectId) loadProject(projectId);
  }, [renderTask.task?.status]);

  const script = project?.data.script;

  const update = (fn: (s: Script) => Script) => {
    if (!project) return;
    setProject({ ...project, data: { ...project.data, script: fn(project.data.script) } });
    setDirty(true);
  };
  const setCast = (i: number, patch: Partial<CastMember>) =>
    update((s) => ({ ...s, cast: s.cast.map((c, j) => (j === i ? { ...c, ...patch } : c)) }));
  const setLine = (i: number, patch: Partial<Line>) =>
    update((s) => ({ ...s, lines: s.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) }));

  const save = async () => {
    if (!project) return false;
    try {
      await engine.put(`/projects/${project.id}`, { script: project.data.script });
      setDirty(false);
      setSaveError(null);
      return true;
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
      return false;
    }
  };

  const render = async (lineIds?: string[]) => {
    if (!project || (dirty && !(await save()))) return;
    renderTask.submit("audiobook.render", {
      project_id: project.id, model: model || undefined, dialogue, qa, format, line_ids: lineIds,
    });
  };

  const selectedVersion = (lineId: string) =>
    project?.renders.find((r) => r.line_id === lineId && r.selected);
  const castIndex = (id: string) => script?.cast.findIndex((c) => c.id === id) ?? 0;
  const rendered = script ? script.lines.filter((l) => selectedVersion(l.id)).length : 0;
  const result = renderTask.task?.status === "succeeded" ? renderTask.task.result : null;
  const unassigned = script?.cast.filter((c) => !c.voice_id).length ?? 0;

  const sourceForm = (
    <>
      <Field label="标题（可选）">
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="不填则由 LLM 起名" />
      </Field>
      <Field label="原文" hint={`${source.length} 字 · ${copy.hint}`}>
        <textarea rows={9} value={source} placeholder={copy.placeholder}
          onChange={(e) => setSource(e.target.value)} />
      </Field>
      <div className="btn-row">
        <Button variant="primary" icon={Sparkles} loading={scriptTask.busy}
          disabled={!source.trim()}
          onClick={() => scriptTask.submit("script.generate",
            { text: source, style, title: title || undefined })}>
          用 LLM 生成剧本
        </Button>
        <span className="small muted">会自动从音色库里按角色描述挑选声音</span>
      </div>
      <TaskProgress task={scriptTask.task} error={scriptTask.submitError} />
    </>
  );

  return (
    <Page app={style} actions={
      <>
        <select value={projectId} onChange={(e) => loadProject(e.target.value)} style={{ width: 220 }}>
          <option value="">{projects.data?.length ? "打开已有项目…" : "还没有项目"}</option>
          {(projects.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
        </select>
        {project && <Button icon={FilePlus2} onClick={() => loadProject("")}>新建</Button>}
      </>
    }>
      {!script ? (
        <Card step={1} title="粘贴原文" sub="第一步：让 LLM 把文字改编成带角色和语气的剧本">
          {sourceForm}
        </Card>
      ) : (
        <>
          <Card step={1} done title={script.title}
            sub={`${script.cast.length} 个角色 · ${script.lines.length} 句台词 · 已生成 ${rendered} 句`}
            actions={
              <Button variant="ghost" size="sm" icon={Wand2} onClick={() => setShowSource(!showSource)}>
                {showSource ? "收起" : "用新原文重新生成"}
              </Button>
            }>
            {showSource && <div style={{ marginTop: 4 }}>{sourceForm}</div>}
          </Card>

          <Card step={2} title="选角" sub={unassigned
            ? `${unassigned} 个角色还没有指定音色，会使用模型自带声音`
            : "每个角色都已指定音色"}>
            <div className="cast-grid">
              {script.cast.map((c, i) => (
                <div key={c.id} className="cast-card">
                  <div className="who"><Avatar name={c.name} index={i} />{c.name}</div>
                  <input value={c.description} placeholder="性别、年龄、性格、音色特点"
                    onChange={(e) => setCast(i, { description: e.target.value })} />
                  <select value={c.voice_id ?? ""}
                    onChange={(e) => setCast(i, { voice_id: e.target.value || null })}>
                    <option value="">模型自带声音</option>
                    {(voices.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </Card>

          <Card step={3} title="台词" sub="可以直接修改文字、说话人和语气，改完记得保存"
            actions={
              <>
                {dirty && <Badge tone="warning">未保存</Badge>}
                <Button size="sm" icon={Save} disabled={!dirty} onClick={save}>保存</Button>
              </>
            }>
            {saveError && <div className="task-error" style={{ marginBottom: 12 }}>{saveError}</div>}
            <div className="lines">
              {script.lines.map((line, i) => {
                const v = selectedVersion(line.id);
                const ci = castIndex(line.speaker);
                return (
                  <div key={line.id} className="line">
                    <span className="no">{i + 1}</span>
                    <div className="speaker-select">
                      <Avatar name={script.cast[ci]?.name ?? "?"} index={ci} size="sm" />
                      <select value={line.speaker} onChange={(e) => setLine(i, { speaker: e.target.value })}>
                        {script.cast.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </select>
                    </div>
                    <textarea value={line.text}
                      rows={Math.min(6, Math.max(1, Math.ceil(line.text.length / 34)))}
                      onChange={(e) => setLine(i, { text: e.target.value })} />
                    <input className="delivery" value={line.delivery} placeholder="语气（可选）"
                      onChange={(e) => setLine(i, { delivery: e.target.value })} />
                    <span className="state">
                      {v ? (
                        <Badge tone={v.qa?.passed === false ? "warning" : "success"}>
                          v{v.version}{v.qa?.passed === false ? " · 质检未过" : ""}
                        </Badge>
                      ) : <Badge>未生成</Badge>}
                    </span>
                    <Button variant="ghost" size="sm" icon={RotateCcw}
                      disabled={renderTask.busy || dialogue}
                      title={dialogue ? "对话模式只能整段重做" : "只重做这一句"}
                      onClick={() => render([line.id])} />
                  </div>
                );
              })}
            </div>
          </Card>

          <Card step={4} title="配音和导出">
            <div className="fields">
              <Field label="配音模型">
                <ModelSelect models={models.data} kinds={dialogue ? ["dialogue"] : ["tts", "voice_design"]}
                  value={model} onChange={setModel} />
              </Field>
              <Field label="导出格式">
                <Segmented value={format} options={FORMATS} onChange={setFormat} block />
              </Field>
            </div>
            <Toggle checked={qa} onChange={setQa} label="质检回听"
              hint="生成后转写回文字和原稿比对，吞字、错字的句子自动重做" />
            <Toggle checked={dialogue} onChange={(v) => { setDialogue(v); setModel(""); }}
              label="对话模式" hint="用多人对话模型整段生成，轮换更自然，但不能逐句重做" />
            <div className="btn-row" style={{ marginTop: 10 }}>
              <Button variant="primary" size="lg" icon={Sparkles} loading={renderTask.busy}
                onClick={() => render()}>
                {rendered === 0 ? "开始配音并混音" : "生成剩下的句子并重新混音"}
              </Button>
            </div>
            <TaskProgress task={renderTask.task} error={renderTask.submitError} />
            {result && (
              <div style={{ marginTop: 16 }} className="stack">
                <Player path={result.output} large label={`成品 · ${result.duration} 秒`} />
                <div className="small muted">字幕文件：<span className="mono">{result.srt}</span></div>
                {result.qa_failed?.length > 0 && (
                  <Warnings items={[`这些句子质检多次未通过，已选用最好的版本：${result.qa_failed.join("、")}`]} />
                )}
                <Warnings items={result.warnings} />
              </div>
            )}
          </Card>
        </>
      )}
      {!script && projects.data?.length === 0 && !scriptTask.task && (
        <Empty title="还没有项目">粘贴一段文字，生成第一个剧本。</Empty>
      )}
    </Page>
  );
}
