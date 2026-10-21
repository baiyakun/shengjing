// 音色库 / 声音分身：导入参考录音（必须确认授权），自动转写文字稿，试听克隆效果。
import { useEffect, useState } from "react";
import {
  Empty, Field, FileField, ModelSelect, Page, Player, Section, TaskProgress, Warnings,
} from "../components";
import { useEngine, useResource, useTask, type ModelInfo, type Voice } from "../engine";

export function Voices() {
  const engine = useEngine();
  const voices = useResource<Voice[]>("/voices");
  const models = useResource<ModelInfo[]>("/catalog");

  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [transcript, setTranscript] = useState("");
  const [tags, setTags] = useState("");
  const [language, setLanguage] = useState("zh");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const transcribe = useTask();

  const [testVoice, setTestVoice] = useState("");
  const [testText, setTestText] = useState("你好，这是用我的声音生成的一段测试语音。");
  const [testModel, setTestModel] = useState("");
  const tts = useTask();

  useEffect(() => {
    if (transcribe.task?.status === "succeeded") voices.reload();
  }, [transcribe.task?.status]);

  const add = async () => {
    setError(null);
    try {
      const res = await engine.post<{ voice: Voice; transcribe_task: { id: string } | null }>(
        "/voices",
        {
          name,
          audio_path: path,
          transcript,
          language,
          tags: tags.split(/[,，\s]+/).filter(Boolean),
          consent: { confirmed: consent },
        },
      );
      if (res.transcribe_task) {
        // 没填文字稿时引擎会自动排一个转写任务，这里接着跟踪它
        transcribe.track(res.transcribe_task.id);
      }
      setName("");
      setPath("");
      setTranscript("");
      setTags("");
      setConsent(false);
      voices.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const result = tts.task?.status === "succeeded" ? tts.task.result : null;

  return (
    <Page
      title="声音分身 / 音色库"
      subtitle="导入一段 10–30 秒清晰的参考录音。克隆别人的声音前必须取得本人授权。"
    >
      <Section title="添加音色">
        <div className="grid">
          <Field label="名称">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：我的声音" />
          </Field>
          <Field label="语言">
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="zh">中文</option>
              <option value="en">英文</option>
            </select>
          </Field>
        </div>
        <FileField label="参考录音" value={path} onChange={setPath} />
        <Field label="参考录音的文字稿" hint="留空会自动转写；文字稿必须和录音内容一致，否则克隆效果会变差。">
          <textarea rows={2} value={transcript} onChange={(e) => setTranscript(e.target.value)} />
        </Field>
        <Field label="标签" hint="用于自动选角，例如：女 年轻 温柔">
          <input value={tags} onChange={(e) => setTags(e.target.value)} />
        </Field>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          我确认这是我本人的声音，或已取得声音所有者的使用授权。
        </label>
        <div className="row">
          <button className="primary" disabled={!name || !path || !consent} onClick={add}>
            添加
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        <TaskProgress task={transcribe.task} error={transcribe.submitError} />
      </Section>

      <Section title={`已有音色（${voices.data?.length ?? 0}）`}>
        {voices.data?.length === 0 && <Empty>还没有音色。</Empty>}
        <div className="voice-list">
          {(voices.data ?? []).map((v) => (
            <div key={v.id} className="voice">
              <div className="row between">
                <b>{v.name}</b>
                <button
                  className="ghost"
                  onClick={async () => {
                    if (window.confirm(`删除音色“${v.name}”？`)) {
                      await engine.del(`/voices/${v.id}`);
                      voices.reload();
                    }
                  }}
                >
                  删除
                </button>
              </div>
              <div className="small muted">{v.tags.join(" · ") || "无标签"}</div>
              <div className="small">{v.transcript || <span className="warn">缺少文字稿</span>}</div>
              <Player path={v.audio_path} />
            </div>
          ))}
        </div>
      </Section>

      <Section title="试听克隆效果">
        <div className="grid">
          <Field label="音色">
            <select value={testVoice} onChange={(e) => setTestVoice(e.target.value)}>
              <option value="">选择音色</option>
              {(voices.data ?? []).map((v) => (
                <option key={v.id} value={v.id}>{v.name}</option>
              ))}
            </select>
          </Field>
          <Field label="模型">
            <ModelSelect models={models.data} kinds={["tts"]} value={testModel} onChange={setTestModel} />
          </Field>
        </div>
        <Field label="文字">
          <textarea rows={2} value={testText} onChange={(e) => setTestText(e.target.value)} />
        </Field>
        <button
          className="primary"
          disabled={!testVoice || !testText || tts.busy}
          onClick={() =>
            tts.submit("tts.quick", { text: testText, voice_id: testVoice, model: testModel || undefined })
          }
        >
          生成
        </button>
        <TaskProgress task={tts.task} error={tts.submitError} />
        {result && (
          <>
            <Player path={result.path} label={`${result.model} · ${result.duration ?? "?"} 秒`} />
            <Warnings items={result.warnings} />
          </>
        )}
      </Section>
    </Page>
  );
}
