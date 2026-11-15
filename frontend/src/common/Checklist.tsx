/** What the operator confirms before a unit or a boat leaves. The server checks it again and records it. */
export function Checklist({ items, en, checked, onChange, el, short }: {
  items: Record<string, string>; en: Record<string, string>; checked: string[];
  onChange: (next: string[]) => void; el: boolean;
  /** Short labels for a compact grid; the full sentence stays as the tooltip. */
  short?: Record<string, [string, string]>;
}) {
  const entries = Object.entries(items);
  if (entries.length === 0) return null;
  const toggle = (k: string) => onChange(checked.includes(k) ? checked.filter((x) => x !== k) : [...checked, k]);
  return (
    <fieldset className={`checklist ${short ? "checklist-short" : ""}`}>
      <legend className="small">{el ? "Πριν από την αποστολή" : "Before sending"}</legend>
      {entries.map(([k, text]) => (
        <label key={k} className="small check" title={short ? (el ? text : en[k] ?? text) : undefined}>
          <input type="checkbox" checked={checked.includes(k)} onChange={() => toggle(k)} />{" "}
          {short?.[k] ? short[k][el ? 0 : 1] : el ? text : en[k] ?? text}
        </label>
      ))}
    </fieldset>
  );
}

export const allChecked = (items: Record<string, string> | undefined, checked: string[]) =>
  Object.keys(items ?? {}).every((k) => checked.includes(k));
