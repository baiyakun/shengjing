// 设置页的两部分：在线 API（四家服务商的 Key 和默认参数）、每类能力的默认模型
import { ChevronDown, ChevronRight, ExternalLink, KeyRound, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Field, ModelSelect } from "../components";
import { useEngine, useResource, type ModelInfo } from "../engine";

interface ProviderOption { key: string; label: string; default: string; hint: string }

interface ProviderInfo {
  id: string;
  name: string;
  docs_url: string;
  key_url: string;
  default_base_url: string;
  base_url_editable: boolean;
  options: ProviderOption[];
  ready: boolean;
  base_url: string;
  values: Record<string, string>;
  models: string[];
}

const PROVIDER_COLORS: Record<string, string> = {
  openai: "#10A37F", senseaudio: "#5856D6", fish_audio: "#FF6B3D", elevenlabs: "#1D1D1F",
};

function ProviderCard({ info, models, onSaved }: {
  info: ProviderInfo;
  models: ModelInfo[] | null;
  onSaved: (list: ProviderInfo[]) => void;
}) {
  const engine = useEngine();
  const [open, setOpen] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(info.base_url);
  const [values, setValues] = useState<Record<string, string>>(info.values);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    setBaseUrl(info.base_url);
    setValues(info.values);
  }, [info.base_url, info.values]);

  const save = async (patch: Record<string, unknown>) => {
    try {
      const list = await engine.put<ProviderInfo[]>(`/providers/${info.id}`, patch);
      setApiKey("");
      setMsg("已保存");
      setTimeout(() => setMsg(null), 2500);
      onSaved(list);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };
  const names = (models ?? []).filter((m) => info.models.includes(m.id))
    .map((m) => m.name.split("·").pop()!.trim());

  return (
    <Card>
      <div className="provider-head" onClick={() => setOpen(!open)} style={{ cursor: "default" }}>
        <span className="tile md" style={{ background: PROVIDER_COLORS[info.id] ?? "#8E8E93" }}>
          <KeyRound size={16} />
        </span>
        <div className="grow">
          <div style={{ fontWeight: 650 }}>{info.name}</div>
          <div className="small muted ellipsis">{names.join(" · ")}</div>
        </div>
        {info.ready ? <Badge tone="success">已连接</Badge> : <Badge>未填写 API Key</Badge>}
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </div>
      {open && (
        <div style={{ marginTop: 16 }}>
          <div className="small" style={{ marginBottom: 12 }}>
            <a href={info.docs_url} target="_blank" rel="noreferrer">接口文档 <ExternalLink size={11} /></a>
            {info.key_url && (
              <> · <a href={info.key_url} target="_blank" rel="noreferrer">获取 API Key <ExternalLink size={11} /></a></>
            )}
          </div>
          <div className="fields">
            <Field label="API Key" hint="保存在系统钥匙串里">
              <input type="password" value={apiKey}
                placeholder={info.ready ? "已保存，留空不修改" : "粘贴 API Key"}
                onChange={(e) => setApiKey(e.target.value)} />
            </Field>
            {info.base_url_editable && (
              <Field label="接口地址" hint="兼容 OpenAI 格式的其他服务也可以填在这里">
                <input value={baseUrl} placeholder={info.default_base_url}
                  onChange={(e) => setBaseUrl(e.target.value)} />
              </Field>
            )}
            {info.options.map((o) => (
              <Field key={o.key} label={o.label} hint={o.hint}>
                <input value={values[o.key] ?? ""} placeholder={o.default || "（可选）"}
                  onChange={(e) => setValues({ ...values, [o.key]: e.target.value })} />
              </Field>
            ))}
          </div>
          <div className="btn-row">
            <Button variant="primary" icon={Save} onClick={() => save({
              base_url: baseUrl, options: values, ...(apiKey ? { api_key: apiKey } : {}),
            })}>保存</Button>
            {info.ready && <Button variant="ghost" onClick={() => save({ api_key: "" })}>删除 API Key</Button>}
            {msg && <span className="small muted">{msg}</span>}
          </div>
        </div>
      )}
    </Card>
  );
}

export function OnlineApis() {
  const providers = useResource<ProviderInfo[]>("/providers");
  const models = useResource<ModelInfo[]>("/catalog");
  return (
    <>
      <p className="small muted" style={{ margin: "0 2px 14px" }}>
        填写 API Key 后，这家服务商的模型就会出现在各页面的模型选择里，也可以在“默认模型”里设为默认。
        使用在线 API 时，文字和音频会上传到服务商，并按其价格计费。
      </p>
      {(providers.data ?? []).map((p) => (
        <ProviderCard key={p.id} info={p} models={models.data} onSaved={(list) => {
          providers.setData(list);
          models.reload();
        }} />
      ))}
    </>
  );
}

const DEFAULT_KINDS: { kind: string; label: string; hint: string }[] = [
  { kind: "tts", label: "朗读 / 克隆", hint: "有声书、播客、声音分身" },
  { kind: "dialogue", label: "多人对话", hint: "有声书和播客的对话模式" },
  { kind: "asr", label: "语音转文字", hint: "会议纪要、质检回听、音色文字稿" },
  { kind: "sfx", label: "音效", hint: "音效库、有声书里的音效" },
  { kind: "music", label: "音乐", hint: "音乐页、有声书里的配乐" },
];

export function DefaultModels({ overrides, onChange }: {
  overrides: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const models = useResource<ModelInfo[]>("/catalog");
  return (
    <Card title="每类能力默认用哪个模型" sub="本地模型和在线 API 可以混用；各页面也可以单独选择">
      {DEFAULT_KINDS.map(({ kind, label, hint }) => (
        <div key={kind} className="setting">
          <div className="grow">
            <div style={{ fontWeight: 600 }}>{label}</div>
            <small>{hint}</small>
          </div>
          <ModelSelect models={models.data} kinds={[kind]} value={overrides[kind] ?? ""}
            onChange={(id) => onChange({ ...overrides, [kind]: id })} />
        </div>
      ))}
    </Card>
  );
}
