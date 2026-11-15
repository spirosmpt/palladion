/** Popup content for clickable map features. Plain DOM, built with textContent (no HTML injection). */
import type { Lang, ScenarioInfo } from "../api";
import { dict, formatMinutes } from "../i18n";
import { hhmm, minutesBetween } from "../time";

type Props = Record<string, unknown>;

/** MapLibre turns nested properties into JSON strings; read them back. */
function parse<T>(v: unknown, fallback: T): T {
  if (typeof v !== "string") return (v as T) ?? fallback;
  try {
    return JSON.parse(v) as T;
  } catch {
    return v as unknown as T;
  }
}

function el(tag: string, cls: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function row(dl: HTMLElement, label: string, value: string) {
  const d = el("div", "");
  d.append(el("dt", "", label), el("dd", "", value));
  dl.append(d);
}

const KIND: Record<Lang, Record<string, string>> = {
  el: { beach: "Παραλία", port: "Λιμάνι / μαρίνα", open_space: "Ανοιχτός χώρος", assembly: "Σημείο συγκέντρωσης" },
  en: { beach: "Beach", port: "Port / marina", open_space: "Open space", assembly: "Assembly point" },
};

export function zonePopup(p: Props, lang: Lang, arrival: { arrival: string | null; now: string } | null, tz: string) {
  const t = dict[lang];
  const root = el("div", "pop");
  const kind = KIND[lang][String(p.kind)] ?? String(p.kind);
  root.append(el("h3", "pop-title", String(p.name || kind)));
  root.append(el("p", "pop-kind", kind));
  const dl = el("dl", "pop-stats");
  const cap = p.capacity_estimate as number | null | undefined;
  if (cap) row(dl, lang === "el" ? "Χωρητικότητα" : "Capacity", `~${cap.toLocaleString(lang === "el" ? "el-GR" : "en-GB")} ${lang === "el" ? "άτομα" : "people"}`);
  if (arrival) {
    if (arrival.arrival) {
      const m = minutesBetween(arrival.now, arrival.arrival);
      row(dl, t.arrivesAt, `${hhmm(arrival.arrival, tz)} (${m > 0 ? t.arrivesIn(m) : t.arrivedAgo(m)})`);
    } else {
      row(dl, t.arrivesAt, lang === "el" ? "δεν φτάνει" : "never");
    }
  }
  root.append(dl);
  const conf = String(p.confidence ?? "approximate") as keyof typeof t.confidence;
  root.append(el("p", "pop-note", `${t.confidence[conf] ?? conf}${p.note ? ` · ${p.note}` : ""}`));
  return root;
}

export function anchorPopup(p: Props, lang: Lang, scenario: ScenarioInfo) {
  const t = dict[lang];
  const tz = scenario.spec.timezone;
  const label = parse<{ el: string; en: string }>(p.label, { el: String(p.id), en: String(p.id) });
  const ids = parse<string[]>(p.source_ids, []);
  const root = el("div", "pop");
  root.append(el("h3", "pop-title", label[lang]));
  const dl = el("dl", "pop-stats");
  const time = String(p.time);
  row(dl, lang === "el" ? "Ώρα" : "Time", hhmm(time, tz));
  const ign = scenario.spec.fire.ignition.time;
  if (time !== ign) row(dl, lang === "el" ? "Από την έναρξη" : "Since ignition", formatMinutes(minutesBetween(ign, time), lang));
  const conf = String(p.confidence) as keyof typeof t.confidence;
  row(dl, lang === "el" ? "Βεβαιότητα" : "Confidence", t.confidence[conf] ?? conf);
  root.append(dl);
  if (p.note) root.append(el("p", "pop-note", String(p.note)));
  if (ids.length) {
    const src = el("p", "pop-sources", `${t.sources}: `);
    ids.forEach((id, i) => {
      const s = scenario.spec.sources.find((x) => x.id === id);
      if (i) src.append(", ");
      if (s) {
        const a = document.createElement("a");
        a.href = s.url;
        a.target = "_blank";
        a.rel = "noreferrer";
        a.textContent = id;
        a.title = s.title;
        src.append(a);
      } else {
        src.append(id);
      }
    });
    root.append(src);
  }
  return root;
}
