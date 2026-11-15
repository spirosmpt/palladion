import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, type CaseSummary } from "../api";
import { useUi } from "../store";
import { hhmm } from "../time";
import { CaseCard } from "./CaseCard";
import { countdown, decisionLabel } from "./labels";
import { NewMessage } from "./NewMessage";

export function useCases() {
  return useQuery({ queryKey: ["cases"], queryFn: api.cases, refetchInterval: 1500, staleTime: 0 });
}

// Greek and Greeklish compare in one Latin form, so "voutza" finds "Νέος Βουτζάς" and "κυανης" finds "kyanis".
const GR: Record<string, string> = {
  α: "a", β: "v", γ: "g", δ: "d", ε: "e", ζ: "z", η: "i", θ: "th", ι: "i", κ: "k", λ: "l", μ: "m", ν: "n", ξ: "x",
  ο: "o", π: "p", ρ: "r", σ: "s", ς: "s", τ: "t", υ: "y", φ: "f", χ: "x", ψ: "ps", ω: "o",
};
const fold = (t: string) =>
  t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/ου/g, "ou").replace(/[α-ως]/g, (ch) => GR[ch] ?? ch)
    .replace(/ch/g, "x").replace(/[yh]/g, "i").replace(/w/g, "o").replace(/ou/g, "u");
const matches = (c: CaseSummary, q: string) =>
  !q || fold([c.id, c.text, c.summary_el ?? "", c.location?.label ?? "", c.channel].join(" ")).includes(fold(q));
/** A medical need with a clock: burns, breathing, unconscious, heart (critical) or injury, infant, pregnant (serious). */
export const medical = (c: CaseSummary) =>
  c.deadline_reason === "medical" || (c.needs?.acuity === "critical" || c.needs?.acuity === "serious");
const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable);

export function CaseQueue({ tz }: { tz: string }) {
  const lang = useUi((s) => s.lang);
  const caseId = useUi((s) => s.caseId);
  const setCaseId = useUi((s) => s.setCaseId);
  const q = useCases();
  const el = lang === "el";
  const [entering, setEntering] = useState(false);
  const [search, setSearch] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const cases = q.data?.cases ?? [];
  const [medOnly, setMedOnly] = useState(false);
  const medCount = cases.filter((c) => medical(c) && ["processing", "proposed", "failed"].includes(c.status)).length;
  const shown = cases.filter((c) => matches(c, search) && (!medOnly || medical(c)));
  const open = shown.filter((c) => ["processing", "proposed", "failed"].includes(c.status));
  const done = shown.filter((c) => !["processing", "proposed", "failed"].includes(c.status));
  const order = [...open, ...done].map((c) => c.id);

  // Keyboard: / search · J/K down/up · Enter open · Esc back or clear · N new message. Navigation only:
  // no key approves, sends or closes anything.
  const keys = useRef({ order, sel, caseId });
  keys.current = { order, sel, caseId };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const { order: ids, sel: cur, caseId: openId } = keys.current;
      if (e.key === "Escape") {
        if (typing(e.target)) { (e.target as HTMLElement).blur(); return; }
        if (openId) { setCaseId(null); setSel(openId); }
        return;
      }
      if (typing(e.target)) return;
      // e.code, not e.key: the same keys work with a Greek keyboard layout (J is "ξ" there).
      if ((e.code === "Slash" || e.key === "/") && !openId) { e.preventDefault(); searchRef.current?.focus(); return; }
      if (openId) return;
      const at = cur ? ids.indexOf(cur) : -1;
      if (e.code === "KeyJ" || (e.key === "ArrowDown" && e.shiftKey)) { e.preventDefault(); setSel(ids[Math.min(ids.length - 1, at + 1)] ?? null); }
      else if (e.code === "KeyK" || (e.key === "ArrowUp" && e.shiftKey)) { e.preventDefault(); setSel(ids[Math.max(0, at - 1)] ?? null); }
      else if (e.key === "Enter" && cur && ids.includes(cur)) { e.preventDefault(); setCaseId(cur); }
      else if (e.code === "KeyN") { e.preventDefault(); setEntering(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCaseId]);
  useEffect(() => {
    if (sel) document.getElementById(`row-${sel}`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  if (caseId) return <CaseCard id={caseId} tz={tz} onBack={() => { setSel(caseId); setCaseId(null); }} />;
  if (!q.data) return <section className="block"><p className="muted">{el ? "Φόρτωση…" : "Loading…"}</p></section>;
  const { counts } = q.data;
  const row = (c: CaseSummary) => (
    <Row key={c.id} c={c} tz={tz} selected={c.id === sel} onOpen={() => { setSel(c.id); setCaseId(c.id); }} />
  );

  return (
    <section className="queue">
      <div className="queue-head">
        <div className="queue-counts">
          <span><b>{counts.open}</b> {el ? "ανοιχτά" : "open"}</span>
          {counts.overdue > 0 && <span className="count-overdue"><b>{counts.overdue}</b> {el ? "το μέτωπο έφτασε" : "front arrived"}</span>}
          {counts.urgent > 0 && <span className="count-urgent"><b>{counts.urgent}</b> {el ? "≤ 10′" : "≤ 10′"}</span>}
          {counts.duplicates > 0 && <span className="count-dup"><b>{counts.duplicates}</b> {el ? "πιθανά διπλά" : "possible duplicates"}</span>}
          <span className="muted"><b>{counts.handled}</b> {el ? "διεκπεραιωμένα" : "handled"}</span>
        </div>
        <div className="queue-tools">
          <input ref={searchRef} className="queue-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder={el ? "Αναζήτηση ( / )" : "Search ( / )"}
            aria-label={el ? "Αναζήτηση στην ουρά: id, οδός, περιοχή, κείμενο" : "Search the queue: id, street, area, text"} />
          <button className={`med-filter ${medOnly ? "on" : ""}`} aria-pressed={medOnly} onClick={() => setMedOnly(!medOnly)}
            title={el ? "Μόνο περιστατικά με ιατρική ανάγκη και ρολόι" : "Only cases with a medical need and clock"}>
            ✚ {medCount}</button>
          {!entering && <button className="new-msg-btn" onClick={() => setEntering(true)} title={el ? "Κλήση ή ασύρματος που δέχτηκες (N)" : "A call or radio message you took (N)"}>
            + {el ? "Νέο μήνυμα" : "New message"}</button>}
        </div>
      </div>
      {entering && <NewMessage tz={tz} onClose={() => setEntering(false)} />}
      {cases.length === 0 && (
        <p className="queue-empty">
          {el
            ? `Δεν έχουν φτάσει μηνύματα ακόμα. ${counts.pending_messages} θα φτάσουν καθώς τρέχει ο χρόνος· πάτα ▶.`
            : `No messages yet. ${counts.pending_messages} will arrive as time runs; press ▶.`}
        </p>
      )}
      {search && <p className="muted small">{el ? `${shown.length} από ${cases.length} ταιριάζουν.` : `${shown.length} of ${cases.length} match.`}{" "}
        <button className="link" onClick={() => setSearch("")}>{el ? "Καθαρισμός" : "Clear"}</button></p>}
      <ol className="queue-list">{open.map(row)}</ol>
      {done.length > 0 && (
        <>
          <h3 className="queue-sub">{el ? "Διεκπεραιωμένα" : "Handled"}</h3>
          <ol className="queue-list queue-done">{done.map(row)}</ol>
        </>
      )}
    </section>
  );
}

function Row({ c, tz, onOpen, selected }: { c: CaseSummary; tz: string; onOpen: () => void; selected: boolean }) {
  const lang = useUi((s) => s.lang);
  const el = lang === "el";
  const decision = c.final_decision ?? c.decision;
  // One status, the one that most needs the operator, instead of a row of tags.
  const tag: [string, string] | null =
    c.status === "processing" ? null
    : c.status === "failed" ? [el ? "θέλει άνθρωπο" : "needs a person", "t-crit"]
    : c.status === "merged" ? [`→ ${c.merged_into}`, "t-done"]
    : c.status === "resolved" ? [el ? "✓ διασώθηκαν" : "✓ rescued", "t-done"]
    : c.status === "closed" ? [el ? "κλειστό" : "closed", "t-done"]
    : ["approved", "changed"].includes(c.status) ? [el ? "✓ εγκρίθηκε" : "✓ approved", "t-done"]
    : c.reopened_by ? [el ? "ξανάνοιξε" : "reopened", "t-warn"]
    : c.duplicate ? [el ? `ίσως = ${c.duplicate.match_id}` : `maybe = ${c.duplicate.match_id}`, "t-dup"]
    : c.contradictions_open?.length ? [el ? "οι πηγές διαφωνούν" : "sources disagree", "t-warn"]
    : c.callback && !c.callback.reply && !c.callback.cancelled ? [el ? "περιμένει απάντηση" : "awaiting reply", "t-dup"]
    : c.no_news ? [el ? `χωρίς νέα ${c.no_news.minutes}′` : `no news ${c.no_news.minutes}′`, "t-warn"]
    : null;
  const where = c.location?.label || (c.location_status === "ambiguous" ? (el ? "θέση ασαφής" : "unclear place") : el ? "χωρίς θέση" : "no place");
  return (
    <li id={`row-${c.id}`}>
      <button className={`case-row u-${c.urgency} s-${c.status}${selected ? " row-sel" : ""}`} onClick={onOpen}
        title={(c.why_here ?? []).map((w) => (el ? w.el : w.en)).join(" ")}>
        <span className="case-clock">
          {c.status === "processing" ? "…" : countdown(c.deadline_left, lang)}
          {c.deadline_reason === "medical" && <span className="med-mark" title={el ? "ιατρική προθεσμία" : "medical clock"}>✚</span>}
        </span>
        <span className="case-main">
          <span className="case-line1">
            <span className={`chip d-${decision ?? "none"}`}>{c.status === "processing" ? (el ? "Διαβάζεται…" : "Reading…") : decisionLabel(decision, lang)}</span>
            {tag && <span className={`case-tag ${tag[1]}`}>{tag[0]}</span>}
          </span>
          <span className="case-line2">{where}{c.people ? ` · ${c.people} ${el ? "άτομα" : c.people === 1 ? "person" : "people"}` : ""}</span>
          <span className="case-line3">{c.summary_el || c.text}</span>
        </span>
        <span className="case-meta">
          <span>{hhmm(c.time, tz)}</span>
          <span className="case-id">{c.id}</span>
        </span>
      </button>
    </li>
  );
}
