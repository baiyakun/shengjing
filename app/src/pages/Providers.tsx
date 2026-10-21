// 设置页的两部分：在线 API（四家服务商的 Key 和默认参数）、每类能力的默认模型
import { useEffect, useState } from "react";
import { Field, ModelSelect, Section } from "../components";
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

function ProviderCard({ info, models, onSaved }: {
  info: ProviderInfo;
  models: ModelInfo[] | null;
  onSaved: (list: ProviderInfo[]) => void;
}) {
  const engine = useEngine();
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
      onSaved(list);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };
  const names = (models ?? []).filter((m) => info.models.includes(m.id)).map((m) => m.name);

  return (
    <div className="provider">
      <div className="row between">
        <b>{info.name}</b>
        <span className={info.ready ? "ok small" : "muted small"}>
          {info.ready ? "已填写 API Key" : "未填写 API Key"}
        </span>
      </div>
      <div className="small muted">
        提供：{names.join("、")} ·{" "}
        <a href={info.docs_url} target="_blank" rel="noreferrer">接口文档</a>
        {info.key_url && (
          <> · <a href={info.key_url} target="_blank" rel="noreferrer">获取 API Key</a></>
        )}
      </div>
      <div className="grid">
        <Field label="API Key" hint="保存在系统钥匙串里">
          <input
            type="password"
            value={apiKey}
            placeholder={info.ready ? "已保存，留空不修改" : ""}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </Field>
        {info.base_url_editable && (
          <Field label="接口地址" hint="兼容 OpenAI 格式的其他服务也可以填在这里">
            <input
              value={baseUrl}
              placeholder={info.default_base_url}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </Field>
        )}
        {info.options.map((o) => (
          <Field key={o.key} label={o.label} hint={o.hint}>
            <input
              value={values[o.key] ?? ""}
              placeholder={o.default || "（可选）"}
              onChange={(e) => setValues({ ...values, [o.key]: e.target.value })}
            />
          </Field>
        ))}
      </div>
      <div className="row">
        <button
          onClick={() =>
            save({
              base_url: baseUrl,
              options: values,
              ...(apiKey ? { api_key: apiKey } : {}),
            })
          }
        >
          保存
        </button>
        {info.ready && (
          <button className="ghost" onClick={() => save({ api_key: "" })}>删除 API Key</button>
        )}
        {msg && <span className="small">{msg}</span>}
      </div>
    </div>
  );
}

export function OnlineApis() {
  const providers = useResource<ProviderInfo[]>("/providers");
  const models = useResource<ModelInfo[]>("/catalog");
  return (
    <Section title="在线 API">
      <p className="small muted">
        填写 API Key 后，对应服务商的语音合成、转写、音效、音乐等就会出现在各页面的模型选择里，
        也可以在下面设为默认。使用在线 API 时，文字和音频会上传到服务商并按其价格计费。
      </p>
      {(providers.data ?? []).map((p) => (
        <ProviderCard
          key={p.id}
          info={p}
          models={models.data}
          onSaved={(list) => {
            providers.setData(list);
            models.reload();
          }}
        />
      ))}
    </Section>
  );
}

const DEFAULT_KINDS: { kind: string; label: string }[] = [
  { kind: "tts", label: "朗读 / 克隆" },
  { kind: "dialogue", label: "多人对话" },
  { kind: "asr", label: "语音转文字" },
  { kind: "sfx", label: "音效" },
  { kind: "music", label: "音乐" },
];

export function DefaultModels({ overrides, onChange }: {
  overrides: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const models = useResource<ModelInfo[]>("/catalog");
  return (
    <Section title="默认模型">
      <p className="small muted">每类能力默认用哪个模型；本地模型和在线 API 可以混用。</p>
      <div className="grid">
        {DEFAULT_KINDS.map(({ kind, label }) => (
          <Field key={kind} label={label}>
            <ModelSelect
              models={models.data}
              kinds={[kind]}
              value={overrides[kind] ?? ""}
              onChange={(id) => onChange({ ...overrides, [kind]: id })}
            />
          </Field>
        ))}
      </div>
    </Section>
  );
}
