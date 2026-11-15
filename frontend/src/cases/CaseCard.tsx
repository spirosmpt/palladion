import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { api, type SecondOpinion, type CaseDetail, type CaseSummary, type Credibility, type FieldOutcome, type HazardReview, type Lang, type LocationReview, type Needs } from "../api";
import { CopyButton } from "../common/CopyButton";
import { MissionInfo, MissionSend, useMissionPlan } from "../missions/MissionBox";
import { useUi } from "../store";
import { hhmm } from "../time";
import { useCases } from "./CaseQueue";
import { DECISIONS, channelLabel, countdown, decisionLabel, flagLabel, vulnerableLabel } from "./labels";

/* One case, in the order an operator works it:
     header   how much time there is, and why
     1 What they say        the message, merged messages, the sender's answers
     2 What the agents found  place, people, needs; what needs a look; how the agents got there (folded)
     3 Proposal             the decision, why, the unit or the question for the sender
     action bar             always at the bottom: approve / send, change, close */

type Span = { start: number; end: number; kind: string; label: string };
const KIND_LABEL: Record<string, string> = {
  people: "άτομα", vulnerable: "ευάλωτοι", situation: "κατάσταση", place: "τόπος", fire: "φωτιά", hazard: "κίνδυνος",
};
/** Flags that are not something to look at: they are said elsewhere on the card, or are only bookkeeping. */
const QUIET_FLAGS = new Set(["pii_masked", "operator_point", "critic_revised", "agent_failed", "area_inferred",
  "location_disputed", "cliff_nearby", "hazard_check_failed"]);

/** Find each verified quote in the message (ignoring case and accents) for highlighting. */
function spans(d: CaseDetail): Span[] {
  const out = d.processed?.intake?.output;
  if (!out) return [];
  const norm = (s: string) => s.normalize("NFD").replace(/\p{Mn}/gu, "").toLowerCase();
  const text = norm(d.text);
  const found: Span[] = [];
  const add = (quote: string | undefined, kind: string, label: string) => {
    if (!quote) return;
    const parts = norm(quote).replace(/…/g, "...").split(/\s*(?:\[\.\.\.\]|\(\.\.\.\)|\.{3,})\s*/);
    let from = 0;
    for (const raw of parts) {
      const part = raw.trim();
      if (!part) continue;
      const i = text.indexOf(part, from);
      if (i < 0) continue;
      found.push({ start: i, end: i + part.length, kind, label });
      from = i + part.length;
    }
  };
  add(out.people?.quote, "people", `${out.people?.count ?? "?"} άτομα`);
  out.vulnerable.forEach((v) => add(v.quote, "vulnerable", v.kind));
  add(out.situation?.quote, "situation", out.situation?.kind ?? "");
  out.places.forEach((p) => add(p.quote, p.role === "fire" ? "fire" : "place", `${p.kind}${p.role !== "person" ? ` (${p.role})` : ""}`));
  out.hazards.forEach((h) => add(h.quote, "hazard", h.kind));
  found.sort((a, b) => b.end - b.start - (a.end - a.start));
  const kept: Span[] = [];
  for (const s of found) if (!kept.some((k) => s.start < k.end && k.start < s.end)) kept.push(s);
  return kept.sort((a, b) => a.start - b.start);
}

function Highlighted({ d }: { d: CaseDetail }) {
  const sp = spans(d);
  const plainLen = d.text.normalize("NFD").replace(/\p{Mn}/gu, "").length;
  if (!sp.length || plainLen !== d.text.length) return <blockquote className="cc-msg">{d.text}</blockquote>;
  const parts: JSX.Element[] = [];
  let at = 0;
  sp.forEach((s, i) => {
    if (s.start > at) parts.push(<span key={`t${i}`}>{d.text.slice(at, s.start)}</span>);
    parts.push(<mark key={`m${i}`} className={`q q-${s.kind}`} title={`${KIND_LABEL[s.kind] ?? s.kind}: ${s.label}`}>{d.text.slice(s.start, s.end)}</mark>);
    at = s.end;
  });
  if (at < d.text.length) parts.push(<span key="end">{d.text.slice(at)}</span>);
  return <blockquote className="cc-msg">{parts}</blockquote>;
}

function Step({ n, title, children, aside }: { n: number; title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="cc-step" aria-label={title}>
      <h3 className="cc-step-title"><span className="cc-n">{n}</span>{title}{aside && <span className="cc-step-aside">{aside}</span>}</h3>
      <div className="cc-step-body">{children}</div>
    </section>
  );
}

export function CaseCard({ id, tz, onBack }: { id: string; tz: string; onBack: () => void }) {
  const lang = useUi((s) => s.lang);
  const el = lang === "el";
  const i = el ? 0 : 1;
  const setRoute = useUi((s) => s.setRoute);
  const setPoint = useUi((s) => s.setPoint);
  const clock = useUi((s) => s.clock);
  const setCaseId = useUi((s) => s.setCaseId);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["case", id], queryFn: () => api.caseDetail(id), refetchInterval: 2000 });
  const [changing, setChanging] = useState(false);
  const [choice, setChoice] = useState<string>("");
  const [note, setNote] = useState("");
  // The version the operator has seen: if the case changes while it is open, an action needs a look first.
  const [seen, setSeen] = useState<number | null>(null);
  const current = q.data?.version ?? null;
  useEffect(() => { if (seen === null && current !== null) setSeen(current); }, [current, seen]);
  useEffect(() => { setSeen(null); setChanging(false); }, [id]);
  const changed = seen !== null && current !== null && current !== seen;

  const act = useMutation({
    mutationFn: (body: Parameters<typeof api.caseAction>[1]) =>
      api.caseAction(id, { ...body, operator: el ? "χειριστής" : "operator", expected_version: seen ?? undefined }),
    onSuccess: (c) => {
      setSeen(c.version); setChanging(false); setNote("");
      void qc.invalidateQueries({ queryKey: ["cases"] });
      void qc.invalidateQueries({ queryKey: ["case", id] });
    },
  });
  const route = useMutation({
    mutationFn: () => api.evacuate(d!.location!.lon, d!.location!.lat, clock?.sim_time ?? d!.time),
    onSuccess: (r) => { setPoint({ lon: d!.location!.lon, lat: d!.location!.lat }); setRoute(r); },
  });
  const all = useCases().data?.cases ?? [];
  const byId = (x: string) => all.find((c) => c.id === x);
  const d = q.data;
  const final = d ? d.final_decision ?? d.decision : null;
  const mission = useMissionPlan(id, !!d?.location && final === "DISPATCH_UNIT_CANDIDATE");

  if (!d) return <section className="block"><button className="link" onClick={onBack}>← {el ? "Ουρά" : "Queue"}</button></section>;
  const proposed = d.decision;
  const open = ["proposed", "failed"].includes(d.status);
  const merged = d.status === "merged";
  const p = d.processed;
  const lr = p?.location_review;
  const canSend = open && !!mission.data?.can_send;

  // What needs a look, one line each, most serious first.
  const attn: { level: "crit" | "warn"; text: ReactNode }[] = [];
  if (d.error) attn.push({ level: "crit", text: el ? "Ο πράκτορας ανάγνωσης δεν απάντησε: διάβασε το μήνυμα και αποφάσισε εσύ." : "The reading agent did not answer: read the message and decide." });
  if (lr?.outcome === "disputed" && lr.proposed) attn.push({ level: "warn", text: <>{el ? "Ο πράκτορας θέσης προτείνει άλλη θέση: " : "The location agent proposes another place: "}<b>{lr.proposed}</b></> });
  if (lr?.outcome === "guess" && lr.proposed) attn.push({ level: "warn", text: <>{el ? "Υπάρχουν ομώνυμοι δρόμοι· πιθανό: " : "Same-name streets; likely: "}<b>{lr.proposed}</b>{el ? " (ρωτάμε τον αποστολέα)" : " (the sender is asked)"}</> });
  if (p?.credibility && p.credibility.assessment !== "credible") attn.push({ level: "warn", text: <>{el ? "Αξιοπιστία: " : "Credibility: "}<b>{p.credibility.assessment === "likely_false" ? (el ? "πιθανώς ψευδές" : "likely false") : (el ? "θέλει επιβεβαίωση" : "needs checking")}</b>{p.credibility.reason_el ? ` · ${p.credibility.reason_el}` : ""}</> });
  (p?.hazard_review?.cautions ?? []).forEach((c) => attn.push({ level: "warn", text: c.text_el }));
  d.flags.filter((f) => !QUIET_FLAGS.has(f)).forEach((f) => attn.push({ level: "warn", text: flagLabel(f, lang) }));
  if (d.no_news) attn.push({ level: "warn", text: <>{el ? `Χωρίς νέα εδώ και ${d.no_news.minutes}′. Ρώτα: ` : `No news for ${d.no_news.minutes}′. Ask: `}«{d.no_news.question_el}» <CopyButton el={el} label={el ? "Αντιγραφή" : "Copy"} text={d.no_news.question_el} /></> });

  const locBy = lr && !lr.error && ["found", "confirmed", "refined"].includes(lr.outcome) ? "agent" : "code";
  const people = d.field.length > 0 && d.people !== null ? `${d.people} ${el ? "μένουν (αναφορά πεδίου)" : "remain (field report)"}` : d.people;

  return (
    <section className={`cc u-${d.urgency}`}>
      <header className="cc-head">
        <div className="cc-top">
          <button className="link" onClick={onBack}>← {el ? "Ουρά" : "Queue"}</button>
          <span className="muted">{d.id} · {hhmm(d.time, tz)} · {channelLabel(d.channel, lang)}</span>
        </div>
        <Clock d={d} tz={tz} lang={lang} />
      </header>

      {merged && d.merged_into && (
        <div className="cc-banner">
          {el ? "Συγχωνεύτηκε με το " : "Merged into "}<button className="link" onClick={() => setCaseId(d.merged_into)}>{d.merged_into}</button>
          {el ? ": οι ενέργειες γίνονται εκεί." : ": act on that case."}{" "}
          <button className="link" onClick={() => act.mutate({ action: "unmerge" })} disabled={act.isPending}>{el ? "Αναίρεση" : "Undo"}</button>
        </div>
      )}
      {d.reopened_by && (
        <div className="cc-banner is-warn">
          {d.reopened_by.startsWith("αναφορά πεδίου")
            ? (el ? "Αναφορά πεδίου ξανάνοιξε το περιστατικό: αποφάσισε ξανά." : "A field report reopened this case: decide again.")
            : (el ? `Νέο μήνυμα (${d.reopened_by}) για τους ίδιους ανθρώπους: αποφάσισε ξανά.` : `A new message (${d.reopened_by}) about the same people: decide again.`)}
        </div>
      )}
      {d.duplicate && (
        <DuplicateBanner d={d} earlier={byId(d.duplicate.match_id)} tz={tz} lang={lang} busy={act.isPending}
          onMerge={() => act.mutate({ action: "merge", target: d.duplicate!.match_id })}
          onDismiss={() => act.mutate({ action: "not_duplicate" })}
          onOpen={() => setCaseId(d.duplicate!.match_id)} />
      )}

      <Step n={1} title={el ? "Τι λέει" : "What they say"}>
        <Highlighted d={d} />
        {d.linked.length > 0 && (
          <ol className="cc-linked">
            {d.linked.map((x) => {
              const c = byId(x);
              return <li key={x}><button className="link" onClick={() => setCaseId(x)}>{x}</button>{c && <> <span className="muted">{hhmm(c.time, tz)}</span> {c.text}</>}</li>;
            })}
          </ol>
        )}
        <AnswerBox d={d} tz={tz} el={el} />
      </Step>

      <Step n={2} title={el ? "Τι βρήκαν οι πράκτορες" : "What the agents found"}>
        <dl className="cc-facts">
          <Fact label={el ? "Θέση" : "Place"} by={d.location ? locBy : null} el={el}>
            {d.location?.label
              ? <>{d.location.label}{d.location.uncertainty_m ? <span className="muted"> ±{Math.round(d.location.uncertainty_m)} m</span> : null}</>
              : <span className="cc-bad">{d.location_status === "ambiguous" ? (el ? "ασαφής: ρωτάμε" : "unclear: asking") : (el ? "άγνωστη" : "unknown")}</span>}
            {lr?.left_from && <span className="muted small"> · {el ? "έφυγαν από " : "left "}{lr.left_from}</span>}
          </Fact>
          <Fact label={el ? "Άτομα" : "People"} el={el}>{people ?? <span className="muted">{el ? "δεν λέει" : "not said"}</span>}</Fact>
          {(d.needs?.needs.length || d.vulnerable.length > 0) ? (
            <Fact label={el ? "Ανάγκες" : "Needs"} el={el}>
              <NeedsLine n={d.needs} vulnerable={d.vulnerable} lang={lang} />
            </Fact>
          ) : null}
          {d.needs?.sender && <Fact label={el ? "Γράφει" : "Writer"} el={el}>{SENDER[d.needs.sender.role]?.[i] ?? d.needs.sender.role}</Fact>}
        </dl>

        {d.field.length > 0 && <FieldBox items={d.field} el={el} />}
        {d.contradictions.length > 0 && <Contradictions d={d} el={el} />}
        {attn.length > 0 && (
          <ul className="cc-attn" aria-label={el ? "Προσοχή" : "Look at"}>
            {attn.map((a, k) => <li key={k} className={`cc-attn-item is-${a.level}`}>{a.text}</li>)}
          </ul>
        )}
        <AgentsDetail d={d} el={el} lang={lang} />
      </Step>

      <Step n={3} title={el ? "Πρόταση" : "Proposal"}
        aside={d.status !== "proposed" && d.status !== "processing" ? <StatusTag d={d} lang={lang} /> : null}>
        <p className="cc-decision"><span className={`chip chip-lg d-${final}`}>{decisionLabel(final, lang)}</span></p>
        <Reasons reasons={d.reasons} el={el} />
        {d.second && <SecondBox s={d.second} el={el} />}
        {d.question_el && (
          <p className="cc-ask"><span className="muted">{el ? "Ερώτηση στον αποστολέα: " : "Question for the sender: "}</span>{d.question_el}{" "}
            <CopyButton el={el} label={el ? "Αντιγραφή" : "Copy"} text={d.question_el} /></p>
        )}
        {d.channel === "live_web" && <ToSenderBox d={d} tz={tz} el={el} />}
        {d.callback && <CallbackBox d={d} tz={tz} el={el} onCancel={() => act.mutate({ action: "cancel_callback" })} busy={act.isPending} />}
        {d.location && !merged && <MissionInfo caseId={d.id} tz={tz} el={el} />}
        {final === "EVACUATE_ROUTE_CANDIDATE" && d.location && (
          <button className="link" onClick={() => route.mutate()} disabled={route.isPending}>
            {el ? "Δείξε τη διαδρομή εκκένωσης στον χάρτη" : "Show the evacuation route on the map"}</button>
        )}
      </Step>

      {!merged && (
        <footer className={`cc-bar ${changed ? "is-held" : ""}`}>
          {changed && (
            <div className="cc-stale">
              <b>{el ? "Το περιστατικό άλλαξε ενώ το έβλεπες." : "The case changed while you were looking."}</b>
              <button className="secondary" onClick={() => setSeen(current)}>{el ? "Το είδα" : "Seen it"}</button>
            </div>
          )}
          {changing ? (
            <div className="cc-change">
              <select value={choice} onChange={(e) => setChoice(e.target.value)} aria-label={el ? "Νέα απόφαση" : "New decision"}>
                {DECISIONS.map((x) => <option key={x} value={x}>{decisionLabel(x, lang)}</option>)}
              </select>
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={el ? "Σημείωση (προαιρετική)" : "Note (optional)"} maxLength={500} />
              <div className="cc-row">
                <button className="primary" disabled={!choice || act.isPending} onClick={() => act.mutate({ action: "change", decision: choice, note: note || undefined })}>
                  {el ? "Αποθήκευση" : "Save"}</button>
                <button className="link" onClick={() => setChanging(false)}>{el ? "Άκυρο" : "Cancel"}</button>
              </div>
            </div>
          ) : open ? (
            <>
              {canSend
                ? <MissionSend caseId={d.id} el={el} caseVersion={changed ? seen : current} onSent={() => act.mutate({ action: "approve" })} />
                : proposed && (
                  <button className="primary cc-primary" onClick={() => act.mutate({ action: "approve" })} disabled={act.isPending}>
                    {el ? "Έγκριση: " : "Approve: "}{decisionLabel(proposed, lang)}</button>
                )}
              <div className="cc-row">
                <button className="secondary" onClick={() => { setChanging(true); setChoice(proposed ?? ""); }}>{el ? "Άλλη απόφαση" : "Another decision"}</button>
                <button className="link" onClick={() => act.mutate({ action: "close" })}>{el ? "Κλείσιμο" : "Close"}</button>
              </div>
            </>
          ) : (
            <div className="cc-row">
              {d.status !== "closed" && <button className="secondary" onClick={() => act.mutate({ action: "close" })}>{el ? "Κλείσιμο περιστατικού" : "Close the case"}</button>}
              <button className="link" onClick={() => act.mutate({ action: "reopen" })}>{el ? "Πίσω στην ουρά" : "Back to the queue"}</button>
            </div>
          )}
          {act.error && <p className="notice">{(act.error as Error).message}</p>}
        </footer>
      )}
    </section>
  );
}

/** The one number that matters first: how long they have, and against what. */
function Clock({ d, tz, lang }: { d: CaseDetail; tz: string; lang: Lang }) {
  const el = lang === "el";
  const medical = d.deadline_reason === "medical";
  const m = d.minutes_left;
  const front = m === null ? (el ? "η φωτιά δεν φτάνει εδώ" : "the fire does not reach here")
    : m <= 0 ? (el ? `το μέτωπο πέρασε πριν από ${Math.round(-m)}′` : `the front passed ${Math.round(-m)}′ ago`)
    : (el ? `μέχρι το μέτωπο · ${d.fire_arrival ? hhmm(d.fire_arrival, tz) : ""}` : `to the front · ${d.fire_arrival ? hhmm(d.fire_arrival, tz) : ""}`);
  return (
    <div className="cc-clock">
      <span className="cc-clock-n">{countdown(d.deadline_left, lang)}</span>
      <span className="cc-clock-what">
        {medical && d.needs ? <b>✚ {el ? `ιατρικό: ${d.needs.summary_el}` : `medical: ${d.needs.summary_el}`}</b> : <b>{front}</b>}
        {medical && <span className="muted">{front}</span>}
      </span>
    </div>
  );
}

function Fact({ label, by, el, children }: { label: string; by?: "agent" | "code" | null; el: boolean; children: ReactNode }) {
  return (
    <div className="cc-fact">
      <dt>{label}</dt>
      <dd>{children}{by && <span className={`cc-by by-${by}`} title={by === "agent" ? (el ? "ο πράκτορας θέσης το έλεγξε με εργαλεία" : "checked by the location agent with tools") : (el ? "από τον κώδικα" : "by code")}>
        {by === "agent" ? (el ? "πράκτορας ✓" : "agent ✓") : (el ? "κώδικας" : "code")}</span>}</dd>
    </div>
  );
}

const ACUITY: Record<string, [string, string]> = {
  critical: ["κρίσιμο", "critical"], serious: ["σοβαρό", "serious"], immobile: ["δεν φεύγουν μόνοι", "cannot leave alone"],
};
const SENDER: Record<string, [string, string]> = {
  on_site: ["από εκεί", "from there"], relative: ["συγγενής, όχι από εκεί", "a relative, not there"],
  repost: ["προωθημένο μήνυμα", "forwarded"], crew: ["πλήρωμα / υπηρεσία", "crew / service"],
};

function NeedsLine({ n, vulnerable, lang }: { n: Needs | null; vulnerable: string[]; lang: Lang }) {
  const el = lang === "el";
  const tags = n?.needs ?? [];
  return (
    <>
      {n?.acuity && <b className={`cc-acuity a-${n.acuity}`}>{ACUITY[n.acuity][el ? 0 : 1]}{n.ambulance ? (el ? " · ασθενοφόρο" : " · ambulance") : ""}</b>}
      {tags.length > 0 ? tags.map((x) => <span key={x.tag} className="cc-tag" title={x.source === "agent" ? (el ? "από τον πράκτορα, ελεγμένο" : "by the agent, checked") : ""}>«{x.quote}»</span>)
        : vulnerable.map((v) => <span key={v} className="cc-tag">{vulnerableLabel(v, lang)}</span>)}
    </>
  );
}

function Reasons({ reasons, el }: { reasons: string[]; el: boolean }) {
  const [all, setAll] = useState(false);
  if (!reasons.length) return null;
  const shown = all ? reasons : reasons.slice(0, 2);
  return (
    <ul className="cc-reasons">
      {shown.map((r) => <li key={r}>{r}</li>)}
      {reasons.length > 2 && !all && <li className="cc-more-li"><button className="link" onClick={() => setAll(true)}>{el ? `+${reasons.length - 2} ακόμα` : `+${reasons.length - 2} more`}</button></li>}
    </ul>
  );
}

function StatusTag({ d, lang }: { d: CaseDetail; lang: Lang }) {
  const el = lang === "el";
  const t = d.status === "approved" ? (el ? "✓ εγκρίθηκε" : "✓ approved")
    : d.status === "changed" ? (el ? `✓ άλλαξε από «${decisionLabel(d.decision, lang)}»` : `✓ changed from "${decisionLabel(d.decision, lang)}"`)
    : d.status === "closed" ? (el ? "κλειστό" : "closed")
    : d.status === "resolved" ? (el ? "✓ διασώθηκαν" : "✓ rescued")
    : d.status === "failed" ? (el ? "θέλει άνθρωπο" : "needs a person") : "";
  return <span className={`cc-status s-${d.status}`}>{t}</span>;
}

/** Every agent that read the message, folded: open it to see how each one got there. */
function AgentsDetail({ d, el, lang }: { d: CaseDetail; el: boolean; lang: Lang }) {
  const p = d.processed;
  if (!p) return null;
  const intake = p.intake;
  const n = [intake, intake?.review, p.location_review, p.hazard_review, p.credibility, d.needs?.check?.startsWith("agent") ? 1 : null]
    .filter(Boolean).length;
  return (
    <details className="cc-agents">
      <summary>{el ? `Πώς το διάβασαν οι πράκτορες (${n})` : `How the agents read it (${n})`}</summary>
      <div className="cc-agents-body">
        {intake?.call && <p className="small">{el ? "Ανάγνωση" : "Reading"}: {intake.call.model} · {intake.call.seconds.toLocaleString(el ? "el" : "en")} s
          {intake.rejected.length > 0 && (el ? ` · ο κώδικας απέρριψε ${intake.rejected.length} παραθέματα` : ` · code dropped ${intake.rejected.length} quotes`)}</p>}
        {intake?.review && <CriticBox r={intake.review} el={el} />}
        {p.location_review && <LocationBox r={p.location_review} el={el} />}
        {p.hazard_review && <HazardBox r={p.hazard_review} el={el} />}
        {p.credibility && <CredibilityBox r={p.credibility} el={el} />}
        {p.arbiter && p.arbiter.rule !== "none" && (
          <p className="small"><b>{el ? "Διαιτητής" : "Arbiter"}:</b> {p.arbiter.why_el}
            {p.arbiter.changed && ` (${decisionLabel(p.arbiter.before, lang)} → ${decisionLabel(p.arbiter.after, lang)})`}</p>
        )}
        {d.pii && d.pii.length > 0 && <p className="muted small">{el ? "Κρύφτηκαν από τους πράκτορες: " : "Hidden from the agents: "}{d.pii.map((k) => PII[el ? 0 : 1][k] ?? k).join(", ")}</p>}
      </div>
    </details>
  );
}

function DuplicateBanner({ d, earlier, tz, lang, busy, onMerge, onDismiss, onOpen }: {
  d: CaseDetail; earlier: CaseSummary | undefined; tz: string; lang: Lang; busy: boolean;
  onMerge: () => void; onDismiss: () => void; onOpen: () => void;
}) {
  const el = lang === "el";
  const dup = d.duplicate!;
  return (
    <div className="cc-banner is-dup" role="region" aria-label={el ? "Πιθανή διπλοεγγραφή" : "Possible duplicate"}>
      <p><b>{el ? "Ίσως οι ίδιοι άνθρωποι με το " : "Maybe the same people as "}</b>
        <button className="link" onClick={onOpen}>{dup.match_id}</button>
        {earlier && <span className="muted"> ({hhmm(earlier.time, tz)})</span>}: {dup.reason_el}</p>
      <div className="cc-row">
        <button className="secondary" onClick={onMerge} disabled={busy}>{el ? `Συγχώνευση με ${dup.match_id}` : `Merge into ${dup.match_id}`}</button>
        <button className="link" onClick={onDismiss} disabled={busy}>{el ? "Διαφορετικό περιστατικό" : "Different case"}</button>
      </div>
    </div>
  );
}

const TRIGGER: Record<string, [string, string]> = {
  rejected_quotes: ["παραθέματα που απέρριψε ο κώδικας", "quotes code rejected"],
  several_numbers: ["πολλοί διαφορετικοί αριθμοί", "several different numbers"],
  more_vulnerable_than_people: ["περισσότεροι ευάλωτοι από άτομα", "more vulnerable people than people"],
  safe_but_asks_for_help: ["«ασφαλείς» αλλά ζητά βοήθεια", "«safe» but asks for help"],
  help_but_says_safe: ["ζητά βοήθεια αλλά λέει «είμαστε καλά»", "asks for help but says «we are fine»"],
  rumour_words: ["λέξεις φήμης", "rumour words"], injection: ["μοιάζει με εντολή προς το σύστημα", "looks like orders to the system"],
};

function CriticBox({ r, el }: { r: NonNullable<NonNullable<NonNullable<CaseDetail["processed"]>["intake"]>["review"]>; el: boolean }) {
  return (
    <details className="cc-agent">
      <summary>{el ? "Ελεγκτής: " : "Critic: "}
        {r.error ? (el ? "απέτυχε" : "failed") : r.revised ? (el ? "διόρθωσε την ανάγνωση" : "corrected the reading")
          : r.objections.length ? (el ? "ενστάσεις χωρίς αλλαγή" : "objections, no change") : (el ? "καμία ένσταση" : "no objection")}</summary>
      <p className="muted small">{el ? "Γιατί: " : "Why: "}{r.triggers.map((t) => TRIGGER[t]?.[el ? 0 : 1] ?? t).join(", ")}</p>
      {r.changes.length > 0 && <ul className="small">{r.changes.map((c) => <li key={c}>{c}</li>)}</ul>}
      {r.objections.length > 0 && <ul className="small">{r.objections.map((o, k) => <li key={k}><b>{o.field}</b>: «{o.quote}»{o.note ? ` · ${o.note}` : ""}</li>)}</ul>}
    </details>
  );
}

const TOOL: Record<string, string> = {
  search_street: "οδός", search_area: "περιοχή", search_landmark: "τοπόσημο", corner: "γωνία",
  place_info: "ακτή και αδιέξοδο", earlier_messages: "προηγούμενα μηνύματα",
};
const LOC_OUTCOME: Record<string, [string, string]> = {
  found: ["βρήκε τη θέση", "found the place"], confirmed: ["επιβεβαίωσε τη θέση", "confirmed the place"],
  refined: ["βρήκε τον δρόμο μέσα στην περιοχή", "found the street inside the area"],
  disputed: ["διαφωνεί· η θέση του κώδικα μένει", "disagrees; the code's place stays"],
  guess: ["ομώνυμοι δρόμοι· ρωτάμε τον αποστολέα", "same-name streets; the sender is asked"],
  no_answer: ["δεν μπόρεσε να ξεχωρίσει", "could not tell"], rejected: ["ο κώδικας απέρριψε την απάντηση", "code refused the answer"],
  failed: ["απέτυχε", "failed"],
};

function LocationBox({ r, el }: { r: LocationReview; el: boolean }) {
  const o = r.error ? "failed" : r.outcome || (r.accepted ? "found" : "no_answer");
  return (
    <details className="cc-agent">
      <summary>{el ? "Θέση: " : "Location: "}{LOC_OUTCOME[o]?.[el ? 0 : 1] ?? o}{r.steps.length > 0 && <span className="muted"> · {r.steps.length} {el ? "αναζητήσεις" : "searches"}</span>}</summary>
      <ol className="small">
        {r.steps.map((s, k) => <li key={k}>{el ? TOOL[s.tool] ?? s.tool : s.tool}: {Object.values(s.args).join(" / ")} → {s.error ?? `${s.results} ${el ? "αποτελέσματα" : "results"}`}</li>)}
      </ol>
      {r.answer?.reason_el && <p className="small">{r.answer.reason_el}</p>}
      {(r.error || (r.reason && (!el || o !== "no_answer"))) && <p className="muted small">{el ? "Έλεγχος κώδικα: " : "Code check: "}{r.reason || r.error}</p>}
    </details>
  );
}

function HazardBox({ r, el }: { r: HazardReview; el: boolean }) {
  return (
    <details className="cc-agent">
      <summary>{el ? "Κίνδυνος: " : "Hazard: "}
        {r.error ? (el ? "δεν έτρεξε" : "did not run") : r.changed ? (el ? "πρότεινε ασφαλέστερη απόφαση" : "proposed a safer decision")
          : r.cautions.length ? `${r.cautions.length} ${el ? "προειδοποιήσεις" : "cautions"}` : (el ? "τίποτα" : "nothing")}</summary>
      <ul className="small">{r.facts.map((f) => <li key={f.id} className={f.severe ? "cc-bad" : ""}>{f.text_el}</li>)}</ul>
      {r.changed && <p className="small">{el ? "Αλλαγή: " : "Change: "}{r.changed.replace("->", "→")}</p>}
      {r.refused && <p className="muted small">{el ? "Ο κώδικας δεν δέχτηκε " : "Code refused "}{r.suggested}: {r.refused}</p>}
    </details>
  );
}

function CredibilityBox({ r, el }: { r: Credibility; el: boolean }) {
  const label = { credible: el ? "αξιόπιστο" : "credible", doubtful: el ? "θέλει επιβεβαίωση" : "needs checking",
    likely_false: el ? "πιθανώς ψευδές" : "likely false" }[r.assessment];
  return (
    <details className="cc-agent">
      <summary>{el ? "Αξιοπιστία: " : "Credibility: "}{label}</summary>
      <ul className="small">{r.signals.map((s) => <li key={s.id}>{s.text_el}{s.quote ? ` «${s.quote}»` : ""}</li>)}</ul>
    </details>
  );
}

const FIELD_STATUS: Record<FieldOutcome["status"], [string, string]> = {
  resolved: ["βγήκαν όλοι", "everyone out"], partial: ["μένουν άτομα", "people remain"],
  not_found: ["δεν βρέθηκε κανείς", "nobody found"], review: ["θέλει έλεγχο", "needs checking"],
};

function FieldBox({ items, el }: { items: FieldOutcome[]; el: boolean }) {
  return (
    <div className="cc-field">
      {items.map((f) => (
        <div key={f.report} className={`cc-field-item fs-${f.status}`}>
          <p><b>{el ? "Πλήρωμα" : "Crew"} {f.unit}: {FIELD_STATUS[f.status][el ? 0 : 1]}</b>
            {f.rescued !== null && <> · {el ? "σώθηκαν" : "rescued"} {f.rescued}</>}
            {f.remaining !== null && f.remaining > 0 && <> · {el ? "μένουν" : "remaining"} {f.remaining}</>}
            {f.simulated && <span className="sim">{el ? "προσομοίωση" : "simulated"}</span>}</p>
          <q className="small">{f.text}</q>
          {[...f.conflicts, ...f.open_items].map((c) => <p key={c} className="cc-attn-item is-warn small">{c}</p>)}
        </div>
      ))}
    </div>
  );
}

const SOURCE: Record<string, [string, string]> = {
  message: ["Μήνυμα", "Message"], linked_message: ["Συγχωνευμένο μήνυμα", "Merged message"],
  fire_model: ["Μοντέλο φωτιάς", "Fire model"], crew: ["Πλήρωμα", "Crew"],
};
const VERDICT: Record<string, [string, string]> = {
  first: ["ισχύει το 1", "1 holds"], second: ["ισχύει το 2", "2 holds"], not_a_contradiction: ["δεν είναι ασυμφωνία", "not a contradiction"],
};

/** Two sources that disagree, side by side. A person says which holds. */
function Contradictions({ d, el }: { d: CaseDetail; el: boolean }) {
  const i = el ? 0 : 1;
  const qc = useQueryClient();
  const decide = useMutation({
    mutationFn: (v: { item: string; verdict: string }) => api.caseContradiction(d.id, v.item, v.verdict, el ? "χειριστής" : "operator"),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["case", d.id] }); void qc.invalidateQueries({ queryKey: ["cases"] }); },
  });
  return (
    <div className="cc-contras">
      {d.contradictions.map((c) => (
        <div key={c.id} className={`cc-contra ${c.decision ? "is-done" : ""}`}>
          <p><b>{el ? "Οι πηγές διαφωνούν" : "Sources disagree"}</b>{c.strength === "strong" && <span className="cc-bad"> · {el ? "ισχυρή" : "strong"}</span>}: {c.text_el}</p>
          <ol className="small">
            {[c.a, c.b].map((s, k) => <li key={k}><span className="muted">{SOURCE[s.source]?.[i] ?? s.source} {s.ref}:</span> «{s.quote}»</li>)}
          </ol>
          {c.decision ? (
            <p className="small">✓ {VERDICT[c.decision.verdict][i]} · {c.decision.by}</p>
          ) : (
            <>
              {c.question_el && <p className="small">{el ? "Ρώτα: " : "Ask: "}«{c.question_el}» <CopyButton el={el} label={el ? "Αντιγραφή" : "Copy"} text={c.question_el} /></p>}
              <div className="cc-row">
                {(["first", "second", "not_a_contradiction"] as const).map((v) => (
                  <button key={v} className="chip-link" disabled={decide.isPending} onClick={() => decide.mutate({ item: c.id, verdict: v })}>{VERDICT[v][i]}</button>
                ))}
              </div>
            </>
          )}
        </div>
      ))}
      {decide.error && <p className="notice">{(decide.error as Error).message}</p>}
    </div>
  );
}

const PII: [Record<string, string>, Record<string, string>] = [
  { phone: "τηλέφωνο", email: "email", afm: "ΑΦΜ", amka: "ΑΜΚΑ", id_card: "ταυτότητα", plate: "πινακίδα", iban: "IBAN", card: "κάρτα" },
  { phone: "phone", email: "email", afm: "tax number", amka: "social security number", id_card: "ID card", plate: "plate", iban: "IBAN", card: "card" },
];

/** What the sender said when someone spoke to them. The case is read again with it. */
function AnswerBox({ d, tz, el }: { d: CaseDetail; tz: string; el: boolean }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const send = useMutation({
    mutationFn: () => api.caseAnswer(d.id, text, el ? "χειριστής" : "operator"),
    onSuccess: () => {
      setText(""); setOpen(false);
      void qc.invalidateQueries({ queryKey: ["case", d.id] });
      void qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
  const real = d.replies.filter((r) => !r.simulated);
  if (d.status === "merged") return null;
  return (
    <>
      {real.map((r, k) => <p key={k} className="cc-reply"><span className="muted">{r.by === "αποστολέας (κινητό)" ? (el ? "Απάντησε από το κινητό" : "Answered from the phone") : (el ? "Απάντηση" : "Answer")} {hhmm(r.at, tz)}:</span> «{r.text}»</p>)}
      {!open ? (
        <button className="link small" onClick={() => setOpen(true)}>+ {el ? "Μίλησα με τον αποστολέα" : "I spoke to the sender"}</button>
      ) : (
        <form className="cc-answer" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
          <textarea rows={2} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} autoFocus
            placeholder={el ? "Τι είπε, με τα λόγια του (π.χ. «Κυκλάδων, δίπλα στην εκκλησία, είμαστε 3»)" : "What they said, in their words"} />
          <div className="cc-row">
            <button className="primary" type="submit" disabled={send.isPending || text.trim().length < 2}>
              {send.isPending ? (el ? "Νέα ανάγνωση…" : "Reading again…") : (el ? "Καταχώριση" : "Enter")}</button>
            <button type="button" className="link" onClick={() => setOpen(false)}>{el ? "Άκυρο" : "Cancel"}</button>
          </div>
          {send.error && <p className="notice">{(send.error as Error).message}</p>}
        </form>
      )}
    </>
  );
}

function CallbackBox({ d, tz, el, onCancel, busy }: { d: CaseDetail; tz: string; el: boolean; onCancel: () => void; busy: boolean }) {
  const cb = d.callback!;
  const waiting = !cb.reply && !cb.cancelled;
  return (
    <div className="cc-callback">
      <p><b>{el ? "Επανάκληση" : "Call-back"}</b>
        <span className="muted"> · {waiting ? (el ? `περιμένει απάντηση${cb.due ? ` (~${hhmm(cb.due, tz)})` : ""}` : `waiting${cb.due ? ` (~${hhmm(cb.due, tz)})` : ""}`)
          : cb.cancelled ? (el ? "σταμάτησε" : "stopped") : (el ? "απάντησε" : "answered")}</span></p>
      <ol className="small">{cb.questions.map((x, k) => <li key={k}>{x.text}</li>)}</ol>
      {cb.reply && <p className="small"><span className="sim">{el ? "προσομοίωση" : "simulated"}</span> «{cb.reply}»</p>}
      {cb.reply && cb.before && (
        <p className="small">{el ? "Μετά την απάντηση: " : "After the reply: "}{decisionLabel(cb.before.decision, el ? "el" : "en")} → <b>{decisionLabel(d.decision, el ? "el" : "en")}</b></p>
      )}
      <div className="cc-row">
        <CopyButton el={el} label={el ? "Αντιγραφή ερωτήσεων" : "Copy the questions"} text={cb.questions.map((x) => x.text).join("\n")} />
        {waiting && <button className="link" onClick={onCancel} disabled={busy}>{el ? "Διακοπή" : "Stop"}</button>}
      </div>
    </div>
  );
}

const pct = (p: number) => `${Math.round(p * 100)}%`;

/** Oumi Iris read the same words. Code compares its answer with the proposal; the operator decides. */
function SecondBox({ s, el }: { s: SecondOpinion; el: boolean }) {
  if (s.error) return <p className="cc-second muted small">{el ? "Δεύτερη γνώμη (Oumi Iris): δεν απάντησε." : "Second opinion (Oumi Iris): no answer."}</p>;
  const v = s.verdict ?? "n/a";
  const head = v === "agrees" ? (el ? "συμφωνεί" : "agrees")
    : v === "disagrees" ? (el ? "διαφωνεί: δες ξανά" : "disagrees: look again")
    : v === "unsure" ? (el ? "αβέβαιη" : "unsure") : "";
  return (
    <div className={`cc-second is-${v}`}>
      <p className="cc-second-head"><b>{el ? "Δεύτερη γνώμη" : "Second opinion"}</b> <span className="muted">Oumi Iris</span> · <b className="cc-second-v">{head}</b></p>
      <ul className="cc-second-bars">
        {(s.top ?? []).map((t) => (
          <li key={t.choice} className={t.choice === s.proposal ? "is-proposal" : ""}>
            <span>{t.el}</span>
            <span className="bar"><i style={{ width: pct(t.p) }} /></span>
            <span>{pct(t.p)}</span>
          </li>
        ))}
      </ul>
      {(s.flags ?? []).includes("iris_urgent") && <p className="small cc-bad">{el ? `Η πρόταση λέει ότι δεν χρειάζεται βοήθεια, η Iris διαβάζει κίνδυνο ζωής (${pct(s.urgent ?? 0)}).` : "The proposal says no help is needed; Iris reads danger to life."}</p>}
      {(s.flags ?? []).includes("iris_not_genuine") && <p className="small cc-bad">{el ? "Η Iris το διαβάζει ως φάρσα ή εντολές προς το σύστημα." : "Iris reads it as a prank or orders to the system."}</p>}
      <p className="muted small">{el ? "Δεν αποφασίζει. Ο κώδικας συγκρίνει με την πρόταση· αποφασίζεις εσύ." : "It does not decide. Code compares it with the proposal; you decide."}</p>
    </div>
  );
}

/** For a message from the public page: a person writes back, and it shows on the sender's phone.
 *  The decision itself reaches the phone when it is approved; this is for anything more. */
function ToSenderBox({ d, tz, el }: { d: CaseDetail; tz: string; el: boolean }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () => api.toSender(d.id, text.trim(), el ? "χειριστής" : "operator"),
    onSuccess: () => { setText(""); void qc.invalidateQueries({ queryKey: ["case", d.id] }); },
  });
  return (
    <div className="cc-tosender">
      <p className="cc-tosender-head"><b>{el ? "Μήνυμα στον αποστολέα" : "Message to the sender"}</b>{" "}
        <span className="muted small">{el ? "φαίνεται στο κινητό του, στη σελίδα πολιτών" : "shows on their phone, on the public page"}</span></p>
      {(d.to_sender ?? []).map((m, k) => <p key={k} className="small">{hhmm(m.at, tz)} · {m.text} <span className="muted">({m.by})</span></p>)}
      <div className="cc-row">
        <input value={text} maxLength={600} onChange={(e) => setText(e.target.value)}
          placeholder={el ? "π.χ. Μείνετε μέσα, έρχεται πυροσβεστικό σε 10′" : "e.g. Stay inside, a fire engine arrives in 10′"}
          onKeyDown={(e) => { if (e.key === "Enter" && text.trim().length >= 2) send.mutate(); }} />
        <button className="secondary" disabled={send.isPending || text.trim().length < 2} onClick={() => send.mutate()}>
          {el ? "Αποστολή" : "Send"}</button>
      </div>
      {send.error && <p className="notice">{(send.error as Error).message}</p>}
    </div>
  );
}
