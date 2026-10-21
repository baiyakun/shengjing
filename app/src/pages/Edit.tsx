// 录音后期：Step-Audio-EditX 改情绪、风格、语速，降噪，去静音，加副语言
import { useState } from "react";
import { Field, FileField, Page, Player, Section, TaskProgress } from "../components";
import { useTask } from "../engine";

const TYPES = [
  { value: "emotion", label: "改情绪", hint: "例如 happy、sad、angry" },
  { value: "style", label: "改风格", hint: "例如 whisper、serious" },
  { value: "speed", label: "改语速", hint: "例如 faster、slower" },
  { value: "denoise", label: "降噪", hint: "" },
  { value: "vad", label: "去掉静音", hint: "" },
  { value: "paralinguistic", label: "加笑声、叹气等", hint: "在目标文字里标出位置" },
];

export function Edit() {
  const [path, setPath] = useState("");
  const [transcript, setTranscript] = useState("");
  const [type, setType] = useState("emotion");
  const [info, setInfo] = useState("happy");
  const [target, setTarget] = useState("");
  const task = useTask();
  const current = TYPES.find((t) => t.value === type)!;
  const needsInfo = ["emotion", "style", "speed"].includes(type);
  const result = task.task?.status === "succeeded" ? task.task.result : null;

  return (
    <Page title="录音后期" subtitle="修改已有的口播录音。Phase 3 会加上按时间轴找口误和单句替换。">
      <Section>
        <FileField label="录音" value={path} onChange={setPath} />
        <Field label="录音的文字稿" hint="留空会先自动转写">
          <textarea rows={2} value={transcript} onChange={(e) => setTranscript(e.target.value)} />
        </Field>
        <div className="grid">
          <Field label="编辑类型">
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </Field>
          {needsInfo && (
            <Field label="目标" hint={current.hint}>
              <input value={info} onChange={(e) => setInfo(e.target.value)} />
            </Field>
          )}
        </div>
        {type === "paralinguistic" && (
          <Field label="目标文字" hint={current.hint}>
            <textarea rows={2} value={target} onChange={(e) => setTarget(e.target.value)} />
          </Field>
        )}
        <button
          className="primary"
          disabled={!path || task.busy}
          onClick={() =>
            task.submit("audio.edit", {
              audio_path: path,
              transcript: transcript || undefined,
              edit_type: type,
              edit_info: needsInfo ? info : undefined,
              target_text: type === "paralinguistic" ? target : undefined,
            })
          }
        >
          开始编辑
        </button>
        <TaskProgress task={task.task} error={task.submitError} />
        {result && (
          <div className="result">
            <Player path={result.input} label="原录音" />
            <Player path={result.path} label="编辑后" />
          </div>
        )}
      </Section>
    </Page>
  );
}
