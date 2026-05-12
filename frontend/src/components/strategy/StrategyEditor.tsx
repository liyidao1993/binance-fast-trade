import { useEffect, useMemo, useState } from "react";
import SchemaForm, {
  defaultsFromSchema,
  pruneEmpty,
  SchemaValue,
} from "./SchemaForm";
import {
  StrategyDTO,
  StrategyType,
  strategiesApi,
} from "@/api/strategies";
import { ApiError } from "@/api/client";

interface Props {
  types: StrategyType[];
  editing: StrategyDTO | null;
  onClose: (saved?: StrategyDTO) => void;
}

export default function StrategyEditor({ types, editing, onClose }: Props) {
  const [name, setName] = useState(editing?.name ?? "");
  const [typeKey, setTypeKey] = useState(editing?.type ?? types[0]?.type_key ?? "");
  const currentType = useMemo(
    () => types.find((t) => t.type_key === typeKey),
    [types, typeKey],
  );

  const [params, setParams] = useState<SchemaValue>(() =>
    editing?.params ?? defaultsFromSchema(currentType?.params_schema),
  );
  const [stoploss, setStoploss] = useState<SchemaValue>(() =>
    editing?.stoploss ?? defaultsFromSchema(currentType?.stoploss_schema),
  );
  const [symbols, setSymbols] = useState<string>(
    (editing?.symbols ?? []).join(","),
  );
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 切换策略类型时重置表单（编辑模式不重置）
  useEffect(() => {
    if (editing) return;
    setParams(defaultsFromSchema(currentType?.params_schema));
    setStoploss(defaultsFromSchema(currentType?.stoploss_schema));
  }, [currentType, editing]);

  async function save() {
    setErr(null);
    setSaving(true);
    try {
      const syms = symbols
        .split(/[,\s]+/)
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean);
      if (syms.length === 0) {
        throw new Error("symbols 至少一个");
      }
      const payload = {
        name,
        type: typeKey,
        params: pruneEmpty(params),
        stoploss: pruneEmpty(stoploss),
        symbols: syms,
      };
      const saved = editing
        ? await strategiesApi.update(editing.id, payload as any)
        : await strategiesApi.create(payload);
      onClose(saved);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="font-semibold">
          {editing ? `编辑策略 #${editing.id}` : "新建策略"}
        </div>
        <button className="btn" onClick={() => onClose()}>
          取消
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="label">名称</label>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="如 btc-grid-1"
          />
        </div>
        <div>
          <label className="label">类型</label>
          <select
            className="input"
            value={typeKey}
            disabled={!!editing}
            onChange={(e) => setTypeKey(e.target.value)}
          >
            {types.map((t) => (
              <option key={t.type_key} value={t.type_key}>
                {t.display_name}（{t.type_key}）
              </option>
            ))}
          </select>
        </div>
        <div className="col-span-2">
          <label className="label">symbols（逗号分隔）</label>
          <input
            className="input"
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            placeholder="BTCUSDT, ETHUSDT"
          />
        </div>
      </div>

      {currentType ? (
        <>
          <section>
            <div className="text-sm font-semibold mb-2 text-accent-info">
              策略参数
            </div>
            <SchemaForm
              schema={currentType.params_schema}
              value={params}
              onChange={setParams}
            />
          </section>

          <section>
            <div className="text-sm font-semibold mb-2 text-accent-warn">
              ★ 止损（必填，启动前后端会强校验）
            </div>
            <SchemaForm
              schema={currentType.stoploss_schema}
              value={stoploss}
              onChange={setStoploss}
            />
          </section>
        </>
      ) : (
        <div className="text-xs text-text-muted">未选择策略类型</div>
      )}

      <div className="flex gap-2">
        <button className="btn flex-1" onClick={() => onClose()}>
          取消
        </button>
        <button
          className="btn-primary flex-1"
          disabled={saving || !name || !typeKey}
          onClick={save}
        >
          {saving ? "保存中…" : editing ? "保存修改" : "创建"}
        </button>
      </div>

      {err && <div className="text-accent-down text-xs break-all">{err}</div>}
    </div>
  );
}
