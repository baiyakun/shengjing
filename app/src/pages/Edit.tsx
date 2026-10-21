// 录音后期：Step-Audio-EditX 改情绪、风格、语速，降噪，去静音，加副语言
import { Eraser, Gauge, Laugh, Palette, Scissors, Smile, Sparkles, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { Button, Callout, Card, DropZone, Field, Page, Player, TaskProgress } from "../components";
import { useResource, useTask, type SystemInfo } from "../engine";

// 预设的值是 Step-Audio-EditX 认的英文写法，按钮上显示中文
type Preset = [value: string, label: string];

const TYPES: { value: string; label: string; desc: string; icon: LucideIcon; presets?: Preset[] }[] = [
  { value: "emotion", label: "改情绪", desc: "开心、难过、生气…", icon: Smile,
    presets: [["happy", "开心"], ["sad", "难过"], ["angry", "生气"], ["surprised", "惊讶"],
      ["fearful", "害怕"], ["calm", "平静"]] },
  { value: "style", label: "改风格", desc: "耳语、严肃、撒娇…", icon: Palette,
    presets: [["whisper", "耳语"], ["serious", "严肃"], ["gentle", "温柔"],
      ["exaggerated", "夸张"], ["childlike", "孩子气"]] },
  { value: "speed", label: "改语速", desc: "更快或更慢", icon: Gauge,
    presets: [["faster", "快一点"], ["slower", "慢一点"], ["more faster", "快很多"],
      ["more slower", "慢很多"]] },
  { value: "denoise", label: "降噪", desc: "去掉背景噪音", icon: Eraser },
  { value: "vad", label: "去掉静音", desc: "删除过长的停顿", icon: Scissors },
  { value: "paralinguistic", label: "加笑声叹气", desc: "在指定位置加入", icon: Laugh },
];

export function Edit() {
  const [path, setPath] = useState("");
  const [transcript, setTranscript] = useState("");
  const [type, setType] = useState("emotion");
  const [info, setInfo] = useState("happy");
  const [target, setTarget] = useState("");
  const task = useTask();
  const system = useResource<SystemInfo>("/system");
  const onlineOnly = system.data?.local.supported === false;
  const current = TYPES.find((t) => t.value === type)!;
  const needsInfo = Boolean(current.presets);
  const result = task.task?.status === "succeeded" ? task.task.result : null;

  return (
    <Page app="edit" narrow>
      {onlineOnly && (
        <Callout>
          录音后期要在本机运行 Step-Audio-EditX，目前没有对应的在线服务。{system.data?.local.reason}。
        </Callout>
      )}
      <Card step={1} title="选择录音">
        <DropZone value={path} onChange={setPath} />
        <div style={{ marginTop: 14 }}>
          <Field label="录音的文字稿（可选）" hint="留空会先自动转写">
            <textarea rows={2} value={transcript} onChange={(e) => setTranscript(e.target.value)} />
          </Field>
        </div>
      </Card>

      <Card step={2} title="想怎么改">
        <div className="choices">
          {TYPES.map((t) => {
            const Icon = t.icon;
            return (
              <button key={t.value} className={`choice ${t.value === type ? "on" : ""}`}
                onClick={() => { setType(t.value); setInfo(t.presets?.[0][0] ?? ""); }}>
                <Icon size={18} color="var(--accent)" />
                <b>{t.label}</b>
                <small>{t.desc}</small>
              </button>
            );
          })}
        </div>
        {needsInfo && (
          <div style={{ marginTop: 16 }}>
            <div className="chips" style={{ marginBottom: 14 }}>
              {current.presets!.map(([value, label]) => (
                <button key={value} className={`chip ${value === info ? "on" : ""}`}
                  onClick={() => setInfo(value)}>{label}</button>
              ))}
            </div>
            <Field label="目标" hint="选上面的预设，或者自己填英文描述">
              <input value={info} onChange={(e) => setInfo(e.target.value)} />
            </Field>
          </div>
        )}
        {type === "paralinguistic" && (
          <div style={{ marginTop: 16 }}>
            <Field label="目标文字" hint="在原文里标出要加入声音的位置，例如：我真的[Laughter]没想到">
              <textarea rows={2} value={target} onChange={(e) => setTarget(e.target.value)} />
            </Field>
          </div>
        )}
        <div className="btn-row" style={{ marginTop: 16 }}>
          <Button variant="primary" size="lg" icon={Sparkles} loading={task.busy}
            disabled={onlineOnly || !path || (needsInfo && !info) || (type === "paralinguistic" && !target)}
            onClick={() => task.submit("audio.edit", {
              audio_path: path,
              transcript: transcript || undefined,
              edit_type: type,
              edit_info: needsInfo ? info : undefined,
              target_text: type === "paralinguistic" ? target : undefined,
            })}>
            开始处理
          </Button>
        </div>
        <TaskProgress task={task.task} error={task.submitError} />
      </Card>

      {result && (
        <Card step={3} done title="前后对比">
          <div className="stack">
            <Player path={result.input} label="原录音" />
            <Player path={result.path} label={`处理后 · ${current.label}`} />
          </div>
        </Card>
      )}
    </Page>
  );
}
