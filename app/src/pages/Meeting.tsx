// 会议纪要：转写并区分发言人（本地分析扩展包，或在线 SenseAudio / ElevenLabs）→ LLM 纪要和待办
import { Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import {
  Badge, Button, Card, DropZone, Field, ModelSelect, Page, Segmented, TaskProgress, Toggle,
  avatarColor, resolveModel,
} from "../components";
import { useResource, useTask, type ModelInfo, type SystemInfo } from "../engine";
import { MEDIA_FILTER } from "../files";

interface Segment { start: number; end: number; text: string; speaker: string | null }

function clock(t: number) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

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
  const segments: Segment[] = result?.segments ?? [];
  const speakerIndex = useMemo(() => {
    const map = new Map<string, number>();
    for (const s of segments) if (s.speaker && !map.has(s.speaker)) map.set(s.speaker, map.size);
    return map;
  }, [segments]);

  return (
    <Page app="meeting">
      <Card>
        <DropZone value={path} onChange={setPath} filter={MEDIA_FILTER} label="选择会议录音或视频"
          hint="支持常见音频和视频格式，点击选择或拖到这里" />
        <div className="fields" style={{ marginTop: 16 }}>
          <Field label="转写模型">
            <ModelSelect models={models.data} kinds={["asr"]} value={model} onChange={setModel} />
          </Field>
          <Field label="语言">
            <Segmented value={language} onChange={setLanguage} block options={[
              { value: "zh", label: "中文 / 中英混合" }, { value: "en", label: "英文" },
            ]} />
          </Field>
          <Field label="发言人数（可选）">
            <input type="number" min={1} max={20} value={speakers} placeholder="自动判断"
              onChange={(e) => setSpeakers(e.target.value)} />
          </Field>
        </div>
        <Toggle checked={diarize && canDiarize} disabled={!canDiarize} onChange={setDiarize}
          label="区分发言人"
          hint={canDiarize ? "转写稿和纪要里会标出每句话是谁说的"
            : "需要安装分析扩展包，或者把转写模型换成 SenseAudio / ElevenLabs 在线转写"} />
        <div className="btn-row" style={{ marginTop: 10 }}>
          <Button variant="primary" size="lg" icon={Sparkles} loading={task.busy} disabled={!path}
            onClick={() => task.submit("meeting.summarize", {
              audio_path: path, language, diarize: diarize && canDiarize,
              model: model || undefined,
              num_speakers: speakers ? Number(speakers) : undefined,
            })}>
            转写并生成纪要
          </Button>
        </div>
        <TaskProgress task={task.task} error={task.submitError} />
      </Card>

      {summary && (
        <div className="cols-main" style={{ marginBottom: 16 }}>
          <Card title={summary.title} sub="会议纪要">
            <p style={{ lineHeight: 1.7 }}>{summary.summary}</p>
            {summary.key_points?.length > 0 && (
              <>
                <div className="divider" />
                <div className="section-title">要点</div>
                <ul className="plain-list">{summary.key_points.map((x: string) => <li key={x}>{x}</li>)}</ul>
              </>
            )}
            {summary.decisions?.length > 0 && (
              <>
                <div className="divider" />
                <div className="section-title">决定</div>
                <ul className="plain-list">{summary.decisions.map((x: string) => <li key={x}>{x}</li>)}</ul>
              </>
            )}
            {summary.open_questions?.length > 0 && (
              <>
                <div className="divider" />
                <div className="section-title">待讨论</div>
                <ul className="plain-list">{summary.open_questions.map((x: string) => <li key={x}>{x}</li>)}</ul>
              </>
            )}
          </Card>
          <Card title="待办" sub={`${summary.action_items?.length ?? 0} 项`}>
            {(summary.action_items ?? []).length === 0 && <p className="muted small">没有提到明确的待办。</p>}
            {(summary.action_items ?? []).map((a: { owner: string; task: string; due: string }, i: number) => (
              <div key={i} className="todo">
                <span className="box" />
                <div>
                  <div>{a.task}</div>
                  <div className="badges" style={{ marginTop: 4 }}>
                    {a.owner && <Badge tone="accent">{a.owner}</Badge>}
                    {a.due && <Badge>{a.due}</Badge>}
                  </div>
                </div>
              </div>
            ))}
          </Card>
        </div>
      )}

      {result && (
        <Card title="转写稿" sub={`${segments.length} 段 · 文件已保存：${result.files.txt}`}>
          <div className="transcript-list">
            {segments.map((s, i) => (
              <div key={i} className="utter">
                <time>{clock(s.start)}</time>
                <div>
                  {s.speaker && (
                    <div className="speaker" style={{ color: avatarColor(speakerIndex.get(s.speaker) ?? 0) }}>
                      {s.speaker}
                    </div>
                  )}
                  <div>{s.text}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </Page>
  );
}
