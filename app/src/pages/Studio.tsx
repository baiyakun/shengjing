// 有声书 / 播客：原文 → LLM 剧本 → 编辑剧本和选角 → 配音（自动质检）→ 混音导出
import { useEffect, useState } from "react";
import {
  Empty, Field, ModelSelect, Page, Player, Section, TaskProgress, Warnings,
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
    title: "有声书 / 广播剧",
    subtitle: "粘贴小说章节，LLM 拆角色、标语气、加音效和配乐，然后逐句配音。",
    placeholder: "粘贴一章小说……",
  },
  podcast: {
    title: "AI 播客",
    subtitle: "给一篇文章或资料，LLM 改写成双人对谈，再配音、加片头音乐。",
    placeholder: "粘贴文章、资料或网页正文……",
  },
} as const;

export function Studio({ style }: { style: "audiobook" | "podcast" }) {
  const engine = useEngine();
  const copy = COPY[style];
  const projects = useResource<ProjectSummary[]>(`/projects?kind=${style}`);
  const voices = useResource<Voice[]>("/voices");
  const models = useResource<ModelInfo[]>("/catalog");
  const [projectId, setProjectId] = useState<string>("");
  const [project, setProject] = useState<Project | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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
      project_id: project.id,
      model: model || undefined,
      dialogue,
      qa,
      format,
      line_ids: lineIds,
    });
  };

  const versions = (lineId: string) => project?.renders.filter((r) => r.line_id === lineId) ?? [];
  const result = renderTask.task?.status === "succeeded" ? renderTask.task.result : null;

  return (
    <Page title={copy.title} subtitle={copy.subtitle}>
      <Section
        title="1. 生成剧本"
        actions={
          <select value={projectId} onChange={(e) => loadProject(e.target.value)}>
            <option value="">打开已有项目…</option>
            {(projects.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>{p.title}</option>
            ))}
          </select>
        }
      >
        <Field label="标题（可选）">
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="原文">
          <textarea
            rows={8}
            value={source}
            placeholder={copy.placeholder}
            onChange={(e) => setSource(e.target.value)}
          />
        </Field>
        <div className="row">
          <button
            className="primary"
            disabled={!source.trim() || scriptTask.busy}
            onClick={() =>
              scriptTask.submit("script.generate", { text: source, style, title: title || undefined })
            }
          >
            用 LLM 生成剧本
          </button>
          <span className="muted small">没有合适音色的角色，需要在下面手动选择或先去音色库添加。</span>
        </div>
        <TaskProgress task={scriptTask.task} error={scriptTask.submitError} />
      </Section>

      {script && (
        <>
          <Section
            title={`2. 编辑剧本：${script.title}`}
            actions={
              <>
                {dirty && <span className="muted small">有未保存的修改</span>}
                <button disabled={!dirty} onClick={save}>保存</button>
              </>
            }
          >
            {saveError && <p className="error">{saveError}</p>}
            <h3>角色和音色</h3>
            <table>
              <thead>
                <tr><th>角色</th><th>描述</th><th>音色</th></tr>
              </thead>
              <tbody>
                {script.cast.map((c, i) => (
                  <tr key={c.id}>
                    <td>{c.name}</td>
                    <td>
                      <input
                        value={c.description}
                        onChange={(e) =>
                          update((s) => {
                            const cast = [...s.cast];
                            cast[i] = { ...c, description: e.target.value };
                            return { ...s, cast };
                          })
                        }
                      />
                    </td>
                    <td>
                      <select
                        value={c.voice_id ?? ""}
                        onChange={(e) =>
                          update((s) => {
                            const cast = [...s.cast];
                            cast[i] = { ...c, voice_id: e.target.value || null };
                            return { ...s, cast };
                          })
                        }
                      >
                        <option value="">模型自带声音</option>
                        {(voices.data ?? []).map((v) => (
                          <option key={v.id} value={v.id}>{v.name}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>台词（{script.lines.length} 句）</h3>
            <table className="lines">
              <thead>
                <tr><th>#</th><th>说话人</th><th>台词</th><th>语气</th><th>版本</th><th /></tr>
              </thead>
              <tbody>
                {script.lines.map((line, i) => {
                  const vs = versions(line.id);
                  const selected = vs.find((v) => v.selected);
                  return (
                    <tr key={line.id}>
                      <td className="muted small">{i + 1}</td>
                      <td>
                        <select
                          value={line.speaker}
                          onChange={(e) =>
                            update((s) => {
                              const lines = [...s.lines];
                              lines[i] = { ...line, speaker: e.target.value };
                              return { ...s, lines };
                            })
                          }
                        >
                          {script.cast.map((c) => (
                            <option key={c.id} value={c.id}>{c.name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="grow">
                        <textarea
                          rows={1}
                          value={line.text}
                          onChange={(e) =>
                            update((s) => {
                              const lines = [...s.lines];
                              lines[i] = { ...line, text: e.target.value };
                              return { ...s, lines };
                            })
                          }
                        />
                      </td>
                      <td>
                        <input
                          value={line.delivery}
                          onChange={(e) =>
                            update((s) => {
                              const lines = [...s.lines];
                              lines[i] = { ...line, delivery: e.target.value };
                              return { ...s, lines };
                            })
                          }
                        />
                      </td>
                      <td className="small">
                        {selected ? (
                          <span className={selected.qa?.passed === false ? "warn" : "ok"}>
                            v{selected.version}
                            {selected.qa?.passed === false ? " 质检未过" : ""}
                          </span>
                        ) : (
                          <span className="muted">未生成</span>
                        )}
                      </td>
                      <td>
                        <button
                          className="ghost"
                          disabled={renderTask.busy || dialogue}
                          title={dialogue ? "对话模式只能整段重做" : "只重做这一句"}
                          onClick={() => render([line.id])}
                        >
                          重做
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Section>

          <Section title="3. 配音和导出">
            <div className="grid">
              <Field label="配音模型">
                <ModelSelect
                  models={models.data}
                  kinds={dialogue ? ["dialogue"] : ["tts", "voice_design"]}
                  value={model}
                  onChange={setModel}
                />
              </Field>
              <Field label="导出格式">
                <select value={format} onChange={(e) => setFormat(e.target.value)}>
                  <option value="mp3">MP3</option>
                  <option value="m4a">M4A</option>
                  <option value="wav">WAV</option>
                  <option value="flac">FLAC</option>
                </select>
              </Field>
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={dialogue}
                onChange={(e) => {
                  setDialogue(e.target.checked);
                  setModel("");
                }}
              />
              对话模式：多人对话模型整段生成，轮换更自然，但不能逐句重做
            </label>
            <label className="check">
              <input type="checkbox" checked={qa} onChange={(e) => setQa(e.target.checked)} />
              质检回听：转写回文字和原稿比对，吞字、错字自动重做
            </label>
            <div className="row">
              <button className="primary" disabled={renderTask.busy} onClick={() => render()}>
                生成缺少的句子并混音
              </button>
            </div>
            <TaskProgress task={renderTask.task} error={renderTask.submitError} />
            {result && (
              <div className="result">
                <Player path={result.output} label={`成品 · ${result.duration} 秒`} />
                <p className="small muted">字幕：{result.srt}</p>
                {result.qa_failed?.length > 0 && (
                  <p className="warn">质检多次未通过：{result.qa_failed.join("、")}（已选用最好的版本）</p>
                )}
                <Warnings items={result.warnings} />
              </div>
            )}
          </Section>
        </>
      )}
      {!script && projects.data?.length === 0 && <Empty>还没有项目。先粘贴原文生成剧本。</Empty>}
    </Page>
  );
}
