import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, setApiLang, type LiveStatus } from "../api";

/** The page for people: write to the operations centre, answer its question, see what help is coming.
 *  The message goes through the same agents and waits for the same person. They see only their own message. */
const EXAMPLES: Record<"el" | "en", string[]> = {
  el: [
    "Είμαστε 4 στην Κυανής Ακτής, η γιαγιά δεν περπατάει, βλέπουμε φλόγες πίσω από το σπίτι",
    "Είμαστε στα βράχια κάτω από την Ποσειδώνος, δεν μπορούμε να ανέβουμε, έχει πολύ καπνό",
    "Ψάχνω τη μητέρα μου Ελένη, 78 χρονών, στο Μάτι, δεν σηκώνει το τηλέφωνο",
  ],
  en: [
    "We are 4 on Kyanis Aktis street, grandma cannot walk, we see flames behind the house",
    "We are on the rocks below Poseidonos street, we cannot climb up, lots of smoke",
    "Looking for my mother Eleni, 78, in Mati, she does not answer her phone",
  ],
};
const WHY: Record<string, [string, string]> = {
  too_fast: ["Πολλά μηνύματα από εσένα. Περίμενε ένα λεπτό.", "Too many messages from you. Wait a minute."],
  busy: ["Η αίθουσα έχει πολλά μηνύματα τώρα. Δοκίμασε σε λίγο.", "The desk is busy right now. Try again shortly."],
  closed: ["Η ζωντανή αποστολή είναι κλειστή.", "Live messages are closed."],
};

const HELP_KIND: Record<string, [string, string]> = { fire_engine: ["πυροσβεστικό", "fire engine"], ambulance: ["ασθενοφόρο", "ambulance"] };

export function SendPage() {
  const [en, setEn] = useState(false);
  const el = !en;
  setApiLang(en ? "en" : "el");
  const [text, setText] = useState("");
  const [sent, setSent] = useState<{ id: string; token: string } | null>(null);
  const info = useQuery({ queryKey: ["live-info"], queryFn: api.liveInfo, refetchInterval: 10_000 });
  const send = useMutation({ mutationFn: () => api.liveSend(text.trim()), onSuccess: (r) => setSent({ id: r.id, token: r.token }) });
  const tz = info.data?.timezone ?? "Europe/Athens";
  const err = send.error ? (send.error as Error).message : null;

  return (
    <div className="pub live">
      <header className="pub-head">
        <h1>{el ? "Μήνυμα στο κέντρο επιχειρήσεων" : "Message the operations centre"}</h1>
        <button className="pub-lang" onClick={() => setEn(!en)}>{en ? "Ελληνικά" : "English"}</button>
      </header>

      {info.data && !info.data.open && <p className="pub-danger">{WHY.closed[el ? 0 : 1]}</p>}

      {!sent ? (
        <form className="live-form" onSubmit={(e) => { e.preventDefault(); if (text.trim().length >= 3) send.mutate(); }}>
          <label htmlFor="live-text">{el ? "Πού είστε, πόσοι είστε και τι συμβαίνει;" : "Where are you, how many, and what is happening?"}</label>
          <textarea id="live-text" value={text} maxLength={600} rows={5} onChange={(e) => setText(e.target.value)}
            placeholder={el ? "π.χ. Είμαστε 3 στη Μαραθώνος, ο δρόμος έκλεισε…" : "e.g. We are 3 on Marathonos Ave, the road is closed…"} />
          <div className="live-examples">
            {EXAMPLES[el ? "el" : "en"].map((x) => (
              <button type="button" key={x} onClick={() => setText(x)}>{x}</button>
            ))}
          </div>
          <button className="live-send" type="submit" disabled={send.isPending || text.trim().length < 3 || info.data?.open === false}>
            {send.isPending ? (el ? "Αποστολή…" : "Sending…") : (el ? "Αποστολή" : "Send")}
          </button>
          {err && <p className="pub-danger live-err">{WHY[err]?.[el ? 0 : 1] ?? (el ? "Δεν στάλθηκε. Δοκίμασε ξανά." : "Not sent. Try again.")}</p>}
        </form>
      ) : (
        <Tracker id={sent.id} token={sent.token} el={el} tz={tz} onNew={() => { setSent(null); setText(""); send.reset(); }} />
      )}
      <p className="pub-drill">{el ? "Επίδειξη. Σε πραγματικό κίνδυνο καλέστε 112." : "A demo. In real danger, call 112."}</p>
    </div>
  );
}

function Tracker({ id, token, el, tz, onNew }: { id: string; token: string; el: boolean; tz: string; onNew: () => void }) {
  const q = useQuery({ queryKey: ["live", id], queryFn: () => api.liveStatus(id), refetchInterval: (s) =>
    (s.state.data as LiveStatus | undefined)?.decided_by_person ? 10_000 : 1500 });
  const d = q.data;
  const at = (iso: string) => new Date(iso).toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz });
  return (
    <section className="live-track" aria-live="polite">
      <p className="live-status">
        {el ? "Ελήφθη" : "Received"} {d ? at(d.received) : ""}.{" "}
        {!d?.read ? (el ? "Διαβάζεται…" : "Being read…")
          : d.help ? null
          : d.decided_by_person ? (el ? "Το κέντρο αποφάσισε." : "The centre has decided.")
          : (el ? "Το βλέπει χειριστής." : "An operator is looking at it.")}
      </p>
      {d?.decided_by_person && (el ? d.advice_el : d.advice_en) && <p className="live-advice">{el ? d.advice_el : d.advice_en}</p>}
      {d?.from_centre.map((m, k) => (
        <p key={k} className="live-centre"><span>{el ? "Από το κέντρο" : "From the centre"} {at(m.at)}</span>{m.text}</p>
      ))}
      {d?.read && d.can_answer && d.questions.length > 0 && <AskBox id={id} token={token} questions={d.questions} el={el} />}
      {d?.answers.map((a, k) => (
        <p key={k} className="live-answered">{el ? "Απαντήσατε" : "You answered"} {at(a.at)}: «{a.text}»</p>
      ))}
      {d?.help && (
        <p className="live-help">{el ? "Έρχεται " : "Coming: "}<b>{d.help.unit}</b> ({HELP_KIND[d.help.kind]?.[el ? 0 : 1] ?? d.help.kind})
          {d.help.phase === "on_scene" ? (el ? " · είναι εκεί" : " · on scene") : <>{el ? " · άφιξη περίπου " : " · expected "}<b>{at(d.help.arrive)}</b></>}</p>
      )}
      <button className="live-send live-new" onClick={onNew}>{el ? "Νέο μήνυμα" : "New message"}</button>
    </section>
  );
}

/** The desk's question, answered from the phone: the agents read the case again with the answer. */
function AskBox({ id, token, questions, el }: { id: string; token: string; questions: string[]; el: boolean }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const answer = useMutation({
    mutationFn: () => api.liveAnswer(id, text.trim(), token),
    onSuccess: (v) => { setText(""); qc.setQueryData(["live", id], v); },
  });
  const err = answer.error ? (answer.error as Error).message : null;
  return (
    <form className="live-ask" onSubmit={(e) => { e.preventDefault(); if (text.trim().length >= 2) answer.mutate(); }}>
      <h2>{el ? "Το κέντρο ρωτά" : "The centre asks"}</h2>
      <ol>{questions.map((q) => <li key={q}>{q}</li>)}</ol>
      <textarea value={text} maxLength={600} rows={3} onChange={(e) => setText(e.target.value)} aria-label={el ? "Η απάντησή σου" : "Your answer"}
        placeholder={el ? "π.χ. Κυκλάδων 4, δίπλα στην εκκλησία, είμαστε 2, δεν έχουμε αυτοκίνητο" : "e.g. 4 Kykladon St, by the church, 2 of us, no car"} />
      <button className="live-send" type="submit" disabled={answer.isPending || text.trim().length < 2}>
        {answer.isPending ? (el ? "Αποστολή…" : "Sending…") : (el ? "Απάντηση" : "Answer")}
      </button>
      {err && <p className="pub-danger live-err">{WHY[err]?.[el ? 0 : 1] ?? (el ? "Δεν στάλθηκε. Δοκίμασε ξανά." : "Not sent. Try again.")}</p>}
    </form>
  );
}
