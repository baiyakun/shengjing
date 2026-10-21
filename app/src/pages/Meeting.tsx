// 会议纪要：转写并区分发言人（本地分析扩展包，或在线 SenseAudio / ElevenLabs）→ LLM 纪要和待办
import { useState } from "react";
import {
  Field, FileField, ModelSelect, Page, Section, TaskProgress, resolveModel,
} from "../components";
import { useResource, useTask, type ModelInfo, type SystemInfo } from "../engine";
import { MEDIA_FILTER } from "../files";

export function Meeting() {
  const system = useResource<SystemInfo>("/system");
  const analysis = system.data?.scheduler.workers.find((w) => w.name === "analysis")?.installed;
  const [path, setPath] = useState("");
  const [language, setLanguage] = useState("zh");
  const [diarize, setDiarize] = useState(true);
  const [speakers, setSpeakers] = useState("");
  const [model, setModel] = useState("");
  const models = useResource<ModelInfo[]>("/catalog");
  const asr = resolveModel(models.data, ["asr"], model);
  // 在线的 SenseAudio、ElevenLabs 转写自带说话人区分；本地需要分析扩展包
  const canDiarize = analysis === true || Boolean(asr?.capabilities.includes("diarization"));
  const task = useTask();
  const result = task.task?.status === "succeeded" ? task.task.result : null;
  const summary = result?.summary;

  return (
    <Page title="会议纪要" subtitle="上传录音或视频，自动转写、区分发言人，并生成纪要和待办。">
      <Section>
        <FileField label="录音或视频" value={path} onChange={setPath} filter={MEDIA_FILTER} />
        <div className="grid">
          <Field label="转写模型">
            <ModelSelect models={models.data} kinds={["asr"]} value={model} onChange={setModel} />
          </Field>
          <Field label="语言">
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="zh">中文 / 中英混合</option>
              <option value="en">英文</option>
            </select>
          </Field>
          <Field label="发言人数（可选）">
            <input type="number" min={1} max={20} value={speakers}
              onChange={(e) => setSpeakers(e.target.value)} />
          </Field>
        </div>
        <label className="check">
          <input type="checkbox" checked={diarize && canDiarize} disabled={!canDiarize}
            onChange={(e) => setDiarize(e.target.checked)} />
          区分发言人{!canDiarize && "（需要分析扩展包，或选择 SenseAudio / ElevenLabs 在线转写）"}
        </label>
        <button
          className="primary"
          disabled={!path || task.busy}
          onClick={() =>
            task.submit("meeting.summarize", {
              audio_path: path, language, diarize: diarize && canDiarize,
              model: model || undefined,
              num_speakers: speakers ? Number(speakers) : undefined,
            })
          }
        >
          开始
        </button>
        <TaskProgress task={task.task} error={task.submitError} />
      </Section>
      {summary && (
        <Section title={summary.title}>
          <p>{summary.summary}</p>
          {summary.key_points?.length > 0 && (<><h3>要点</h3><ul>{summary.key_points.map((x: string) => <li key={x}>{x}</li>)}</ul></>)}
          {summary.decisions?.length > 0 && (<><h3>决定</h3><ul>{summary.decisions.map((x: string) => <li key={x}>{x}</li>)}</ul></>)}
          {summary.action_items?.length > 0 && (
            <>
              <h3>待办</h3>
              <table>
                <thead><tr><th>负责人</th><th>事项</th><th>截止</th></tr></thead>
                <tbody>
                  {summary.action_items.map((a: { owner: string; task: string; due: string }, i: number) => (
                    <tr key={i}><td>{a.owner}</td><td>{a.task}</td><td>{a.due}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Section>
      )}
      {result && (
        <Section title="转写稿">
          <p className="small muted">文件：{result.files.txt} · {result.files.srt}</p>
          <pre className="transcript">{result.transcript}</pre>
        </Section>
      )}
    </Page>
  );
}
