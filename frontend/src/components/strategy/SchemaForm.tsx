import { useMemo } from "react";

type Schema = {
  type?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  enum?: any[];
  default?: any;
  description?: string;
  minimum?: number;
  maximum?: number;
  format?: string;
};

export type SchemaValue = Record<string, any>;

/** 从 JSON Schema 推断初始表单值. */
export function defaultsFromSchema(schema: Schema | undefined): SchemaValue {
  const out: SchemaValue = {};
  const props = schema?.properties ?? {};
  for (const [k, v] of Object.entries(props)) {
    if (v.default !== undefined) out[k] = v.default;
    else if (v.type === "boolean") out[k] = false;
    else if (v.type === "integer" || v.type === "number") out[k] = "";
    else out[k] = "";
  }
  return out;
}

/** 通用 JSON Schema 表单（只支持扁平 properties）. */
export default function SchemaForm({
  schema,
  value,
  onChange,
  disabled = false,
}: {
  schema: Schema;
  value: SchemaValue;
  onChange: (v: SchemaValue) => void;
  disabled?: boolean;
}) {
  const required = useMemo(() => new Set(schema.required ?? []), [schema.required]);
  const props = schema.properties ?? {};

  function set(k: string, v: any) {
    onChange({ ...value, [k]: v });
  }

  return (
    <div className="grid grid-cols-2 gap-2">
      {Object.entries(props).map(([k, s]) => {
        const req = required.has(k);
        const label = (
          <label className="label">
            {k}
            {req && <span className="text-accent-down ml-0.5">*</span>}
          </label>
        );

        // enum → select
        if (Array.isArray(s.enum)) {
          return (
            <div key={k}>
              {label}
              <select
                disabled={disabled}
                value={value[k] ?? ""}
                onChange={(e) => set(k, e.target.value)}
                className="input"
              >
                <option value="" disabled>
                  请选择…
                </option>
                {s.enum.map((opt) => (
                  <option key={String(opt)} value={String(opt)}>
                    {String(opt)}
                  </option>
                ))}
              </select>
              {s.description && (
                <div className="text-[10px] text-text-muted mt-0.5">
                  {s.description}
                </div>
              )}
            </div>
          );
        }

        // boolean → checkbox
        if (s.type === "boolean") {
          return (
            <label
              key={k}
              className="flex items-center gap-2 text-xs col-span-2"
            >
              <input
                type="checkbox"
                disabled={disabled}
                checked={!!value[k]}
                onChange={(e) => set(k, e.target.checked)}
              />
              <span>
                {k}
                {req && <span className="text-accent-down ml-0.5">*</span>}
              </span>
              {s.description && (
                <span className="text-text-muted ml-2">{s.description}</span>
              )}
            </label>
          );
        }

        // integer / number
        if (s.type === "integer" || s.type === "number") {
          return (
            <div key={k}>
              {label}
              <input
                disabled={disabled}
                type="number"
                inputMode="decimal"
                step={s.type === "integer" ? 1 : "any"}
                min={s.minimum}
                max={s.maximum}
                value={value[k] ?? ""}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === "") set(k, "");
                  else set(k, s.type === "integer" ? parseInt(raw, 10) : Number(raw));
                }}
                className="input num"
              />
              {s.description && (
                <div className="text-[10px] text-text-muted mt-0.5">
                  {s.description}
                </div>
              )}
            </div>
          );
        }

        // 默认 string
        return (
          <div key={k}>
            {label}
            <input
              disabled={disabled}
              type="text"
              value={value[k] ?? ""}
              onChange={(e) => set(k, e.target.value)}
              className="input"
              placeholder={s.description || ""}
            />
            {s.description && (
              <div className="text-[10px] text-text-muted mt-0.5">
                {s.description}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 清洗：去掉空字符串字段，让后端拿到更干净的 payload. */
export function pruneEmpty(v: SchemaValue): SchemaValue {
  const out: SchemaValue = {};
  for (const [k, val] of Object.entries(v)) {
    if (val === "" || val === null || val === undefined) continue;
    out[k] = val;
  }
  return out;
}
