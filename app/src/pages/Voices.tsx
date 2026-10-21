// 声音分身 / 音色库：导入参考录音（必须确认授权），自动转写文字稿，试听克隆效果。
import { Plus, Sparkles, Trash2, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Avatar, Badge, Button, Card, DropZone, Empty, Field, FitWarning, Modal, ModelSelect, Page,
  Player, Segmented, TaskProgress, Toggle, Warnings, resolveModel,
} from "../components";
import { useEngine, useResource, useTask, type ModelInfo, type Voice } from "../engine";

function AddVoice({ onClose, onAdded }: {
  onClose: () => void;
  onAdded: (voice: Voice, transcribeTaskId: string | null) => void;
}) {
  const engine = useEngine();
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [transcript, setTranscript] = useState("");
  const [tags, setTags] = useState("");
  const [language, setLanguage] = useState("zh");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await engine.post<{ voice: Voice; transcribe_task: { id: string } | null }>(
        "/voices",
        {
          name, audio_path: path, transcript, language,
          tags: tags.split(/[,，\s]+/).filter(Boolean),
          consent: { confirmed: consent },
        },
      );
      onAdded(res.voice, res.transcribe_task?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="添加音色" onClose={onClose} footer={
      <>
        <Button onClick={onClose}>取消</Button>
        <Button variant="primary" loading={busy} disabled={!name || !path || !consent} onClick={add}>
          添加
        </Button>
      </>
    }>
      <div className="stack">
        <DropZone value={path} onChange={setPath} label="选择参考录音"
          hint="10–30 秒、安静环境下的清晰录音效果最好" />
        <div className="fields">
          <Field label="名称">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：我的声音" />
          </Field>
          <Field label="语言">
            <Segmented value={language} onChange={setLanguage} block
              options={[{ value: "zh", label: "中文" }, { value: "en", label: "英文" }]} />
          </Field>
        </div>
      </div>
      <Field label="参考录音的文字稿" hint="留空会自动转写。文字稿必须和录音内容一致，否则克隆效果会变差。">
        <textarea rows={3} value={transcript} onChange={(e) => setTranscript(e.target.value)} />
      </Field>
      <Field label="标签" hint="用于自动选角，例如：女 年轻 温柔">
        <input value={tags} onChange={(e) => setTags(e.target.value)} />
      </Field>
      <Toggle checked={consent} onChange={setConsent} label="我确认拥有这个声音的使用授权"
        hint="这是我本人的声音，或已取得声音所有者的同意。" />
      {error && <div className="task-error">{error}</div>}
    </Modal>
  );
}

export function Voices() {
  const engine = useEngine();
  const voices = useResource<Voice[]>("/voices");
  const models = useResource<ModelInfo[]>("/catalog");
  const [adding, setAdding] = useState(false);
  const transcribe = useTask();

  const [testVoice, setTestVoice] = useState("");
  const [testText, setTestText] = useState("你好，这是用我的声音生成的一段测试语音。");
  const [testModel, setTestModel] = useState("");
  const [delivery, setDelivery] = useState("");
  const tts = useTask();

  useEffect(() => {
    if (transcribe.task?.status === "succeeded") voices.reload();
  }, [transcribe.task?.status]);
  useEffect(() => {
    if (!testVoice && voices.data?.length) setTestVoice(voices.data[0].id);
  }, [voices.data]);

  const result = tts.task?.status === "succeeded" ? tts.task.result : null;
  const list = voices.data ?? [];

  return (
    <Page app="voices" actions={
      <Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>添加音色</Button>
    }>
      {adding && (
        <AddVoice onClose={() => setAdding(false)} onAdded={(_, taskId) => {
          setAdding(false);
          voices.reload();
          if (taskId) transcribe.track(taskId);
        }} />
      )}
      {transcribe.task && transcribe.task.status !== "succeeded" && (
        <Card title="正在为新音色转写文字稿">
          <TaskProgress task={transcribe.task} />
        </Card>
      )}

      <div className="cols-main">
        <div>
          {list.length === 0 ? (
            <Card>
              <Empty icon={UserRound} title="还没有音色">
                导入一段 10–30 秒的录音，就能用这个声音朗读任意文字。
              </Empty>
              <div style={{ textAlign: "center" }}>
                <Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>添加第一个音色</Button>
              </div>
            </Card>
          ) : (
            <div className="voice-grid">
              {list.map((v, i) => (
                <Card key={v.id} className="voice-card">
                  <div className="who">
                    <Avatar name={v.name} index={i} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="title" style={{ fontWeight: 650 }}>{v.name}</div>
                      <div className="badges">
                        <Badge>{v.language === "en" ? "英文" : "中文"}</Badge>
                        {v.tags.map((t) => <Badge key={t}>{t}</Badge>)}
                      </div>
                    </div>
                    <Button variant="ghost" size="sm" icon={Trash2} title="删除" onClick={async () => {
                      if (window.confirm(`删除音色“${v.name}”？`)) {
                        await engine.del(`/voices/${v.id}`);
                        voices.reload();
                      }
                    }} />
                  </div>
                  <div className="small" style={{ color: v.transcript ? "var(--text-2)" : "var(--warning)" }}>
                    {v.transcript ? `“${v.transcript}”` : "缺少文字稿（正在转写或转写失败）"}
                  </div>
                  <Player path={v.audio_path} />
                </Card>
              ))}
            </div>
          )}
        </div>

        <Card title="试听克隆效果" sub="用选中的声音读一段文字">
          <Field label="音色">
            <select value={testVoice} onChange={(e) => setTestVoice(e.target.value)}>
              {list.length === 0 && <option value="">先添加一个音色</option>}
              {list.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          </Field>
          <Field label="模型">
            <ModelSelect models={models.data} kinds={["tts"]} value={testModel} onChange={setTestModel} />
          </Field>
          <FitWarning model={resolveModel(models.data, ["tts"], testModel)} />
          <Field label="文字">
            <textarea rows={4} value={testText} onChange={(e) => setTestText(e.target.value)} />
          </Field>
          <Field label="语气（可选）" hint="支持的模型会按描述调整语气，例如：开心、轻声、带点疲惫">
            <input value={delivery} onChange={(e) => setDelivery(e.target.value)} />
          </Field>
          <Button variant="primary" icon={Sparkles} loading={tts.busy}
            disabled={!testVoice || !testText.trim()}
            onClick={() => tts.submit("tts.quick", {
              text: testText, voice_id: testVoice, model: testModel || undefined,
              delivery: delivery || undefined,
            })}>
            生成
          </Button>
          <TaskProgress task={tts.task} error={tts.submitError} />
          {result && (
            <div style={{ marginTop: 14 }}>
              <Player path={result.path} label={`${result.model} · ${result.duration ?? "?"} 秒`} />
              <div style={{ marginTop: 10 }}><Warnings items={result.warnings} /></div>
            </div>
          )}
        </Card>
      </div>
    </Page>
  );
}
