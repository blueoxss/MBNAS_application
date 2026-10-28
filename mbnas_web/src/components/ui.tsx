/** Small presentational building blocks: badges, banners, labelled form fields, key/value lists. */
import { useEffect, useId, useState, type ReactNode } from 'react';

export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'neutral';

export function Badge({ tone = 'neutral', children, title, className = '' }: { tone?: Tone; children: ReactNode; title?: string; className?: string }): JSX.Element {
  return (
    <span className={`badge b-${tone} ${className}`} title={title}>
      {children}
    </span>
  );
}

export function Banner({ tone, children, role }: { tone: 'info' | 'warn' | 'err' | 'ok'; children: ReactNode; role?: 'alert' | 'status' }): JSX.Element {
  const icon = tone === 'err' ? '✖ ' : tone === 'warn' ? '⚠ ' : tone === 'ok' ? '✔ ' : 'ℹ ';
  return (
    <div className={`banner banner-${tone}`} role={role ?? (tone === 'err' ? 'alert' : 'status')}>
      <span aria-hidden="true">{icon}</span>
      {children}
    </div>
  );
}

export function Card({ title, children, actions, id }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; id?: string }): JSX.Element {
  return (
    <section className="card" id={id} aria-labelledby={id ? `${id}-t` : undefined}>
      {(title || actions) && (
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
          {title && <h2 id={id ? `${id}-t` : undefined} style={{ margin: 0 }}>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

interface FieldShell {
  label: ReactNode;
  unit?: string;
  help?: ReactNode;
  error?: string | null;
  testId?: string;
}

function Shell({ label, unit, help, error, id, children }: FieldShell & { id: string; children: ReactNode }): JSX.Element {
  return (
    <div className="field">
      <label htmlFor={id}>
        {label} {unit && <span className="unit">[{unit}]</span>}
      </label>
      {children}
      {help && <span className="help" id={`${id}-h`}>{help}</span>}
      {error && (
        <span className="field-err" id={`${id}-e`} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

function describedBy(id: string, help: unknown, error: unknown): string | undefined {
  const ids = [help ? `${id}-h` : '', error ? `${id}-e` : ''].filter(Boolean).join(' ');
  return ids || undefined;
}

export function TextField(p: FieldShell & { value: string; onChange: (v: string) => void; onBlur?: () => void; readOnly?: boolean; placeholder?: string; mono?: boolean; type?: 'text' | 'password' | 'url'; autoComplete?: string }): JSX.Element {
  const id = useId();
  return (
    <Shell {...p} id={id}>
      <input
        id={id}
        type={p.type ?? 'text'}
        className={p.mono ? 'mono' : undefined}
        value={p.value}
        readOnly={p.readOnly}
        placeholder={p.placeholder}
        autoComplete={p.autoComplete ?? 'off'}
        spellCheck={false}
        aria-invalid={p.error ? true : undefined}
        aria-describedby={describedBy(id, p.help, p.error)}
        data-testid={p.testId}
        onChange={(e) => p.onChange(e.target.value)}
        onBlur={p.onBlur}
      />
    </Shell>
  );
}

function fmtNumber(v: number | null | undefined): string {
  return typeof v === 'number' && Number.isFinite(v) ? String(v) : '';
}

/** Number input that keeps what the user typed; commits `null` for empty/invalid text. */
export function NumberField(p: FieldShell & { value: number | null | undefined; onChange?: (v: number | null) => void; readOnly?: boolean; step?: number | 'any'; min?: number; max?: number; placeholder?: string }): JSX.Element {
  const id = useId();
  const [txt, setTxt] = useState(fmtNumber(p.value));
  useEffect(() => {
    const parsed = txt.trim() === '' ? null : Number(txt);
    const same = parsed === (p.value ?? null) || (Number.isNaN(parsed as number) && p.value == null);
    if (!same) setTxt(fmtNumber(p.value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.value]);
  return (
    <Shell {...p} id={id}>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        value={txt}
        readOnly={p.readOnly}
        placeholder={p.placeholder}
        step={p.step ?? 'any'}
        min={p.min}
        max={p.max}
        aria-invalid={p.error ? true : undefined}
        aria-describedby={describedBy(id, p.help, p.error)}
        data-testid={p.testId}
        onChange={(e) => {
          setTxt(e.target.value);
          const t = e.target.value.trim();
          const n = t === '' ? null : Number(t);
          p.onChange?.(n === null || Number.isNaN(n) ? null : n);
        }}
      />
    </Shell>
  );
}

/** Comma-separated list of integers (seeds). Commits the parsed list; non-integers produce NaN entries so the validator flags them. */
export function IntListField(p: FieldShell & { value: number[] | undefined; onChange: (v: number[]) => void }): JSX.Element {
  const id = useId();
  const [txt, setTxt] = useState((p.value ?? []).join(', '));
  useEffect(() => {
    const cur = parse(txt);
    if (JSON.stringify(cur) !== JSON.stringify(p.value ?? [])) setTxt((p.value ?? []).join(', '));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.value]);
  function parse(t: string): number[] {
    return t.split(',').map((s) => s.trim()).filter((s) => s !== '').map(Number);
  }
  return (
    <Shell {...p} id={id}>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        value={txt}
        aria-invalid={p.error ? true : undefined}
        aria-describedby={describedBy(id, p.help, p.error)}
        data-testid={p.testId}
        onChange={(e) => {
          setTxt(e.target.value);
          p.onChange(parse(e.target.value));
        }}
      />
    </Shell>
  );
}

export function SelectField(p: FieldShell & { value: string; onChange: (v: string) => void; options: readonly (string | { value: string; label: string })[]; allowEmpty?: string }): JSX.Element {
  const id = useId();
  return (
    <Shell {...p} id={id}>
      <select id={id} value={p.value} onChange={(e) => p.onChange(e.target.value)} aria-describedby={describedBy(id, p.help, p.error)} data-testid={p.testId}>
        {p.allowEmpty !== undefined && <option value="">{p.allowEmpty}</option>}
        {p.options.map((o) => {
          const v = typeof o === 'string' ? o : o.value;
          const l = typeof o === 'string' ? o : o.label;
          return (
            <option key={v} value={v}>
              {l}
            </option>
          );
        })}
      </select>
    </Shell>
  );
}

export function CheckField({ label, checked, onChange, disabled, help, testId }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; help?: ReactNode; testId?: string }): JSX.Element {
  const id = useId();
  return (
    <label className={`check${disabled ? ' disabled' : ''}`} htmlFor={id}>
      <input id={id} type="checkbox" checked={checked} disabled={disabled} data-testid={testId} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        {help && <span className="help" style={{ display: 'block' }}>{help}</span>}
      </span>
    </label>
  );
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }): JSX.Element {
  return (
    <dl className="kv">
      {rows.map(([k, v], i) => (
        <div key={i} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Compact number input for table cells; the accessible name comes from `label` (aria-label). */
export function CellNumber({ label, value, onChange, readOnly, step, testId, invalid }: { label: string; value: number | null | undefined; onChange?: (v: number | null) => void; readOnly?: boolean; step?: number | 'any'; testId?: string; invalid?: boolean }): JSX.Element {
  const [txt, setTxt] = useState(fmtNumber(value));
  useEffect(() => {
    const parsed = txt.trim() === '' ? null : Number(txt);
    const same = parsed === (value ?? null) || (Number.isNaN(parsed as number) && value == null);
    if (!same) setTxt(fmtNumber(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <input
      type="number"
      inputMode="decimal"
      aria-label={label}
      value={txt}
      readOnly={readOnly}
      step={step ?? 'any'}
      aria-invalid={invalid ? true : undefined}
      data-testid={testId}
      style={{ minWidth: 84 }}
      onChange={(e) => {
        setTxt(e.target.value);
        const t = e.target.value.trim();
        const n = t === '' ? null : Number(t);
        onChange?.(n === null || Number.isNaN(n) ? null : n);
      }}
    />
  );
}
