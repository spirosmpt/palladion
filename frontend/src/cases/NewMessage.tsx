import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { useUi } from "../store";
import { hhmm } from "../time";
import { ENTRY_CHANNELS, channelLabel } from "./labels";

/** A call or radio message the operator types in. It goes through the same agents and waits for a person. */
export function NewMessage({ tz, onClose }: { tz: string; onClose: () => void }) {
  const lang = useUi((s) => s.lang);
  const el = lang === "el";
  const clock = useUi((s) => s.clock);
  const point = useUi((s) => s.point);
  const setPoint = useUi((s) => s.setPoint);
  const setCaseId = useUi((s) => s.setCaseId);
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [channel, setChannel] = useState<string>("199_call");
  const [ago, setAgo] = useState(0);
  const [usePoint, setUsePoint] = useState(false);

  const now = clock ? new Date(clock.sim_time) : null;
  const received = now ? new Date(now.getTime() - ago * 60_000) : null;
  const submit = useMutation({
    mutationFn: () => api.caseSubmit({
      text, channel, operator: el ? "χειριστής" : "operator",
      received_at: received?.toISOString(),
      ...(usePoint && point ? { lon: point.lon, lat: point.lat } : {}),
    }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["cases"] });
      setPoint(null);
      onClose();
      setCaseId(r.id);
    },
  });

  return (
    <form className="new-message block" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
      <div className="row spread">
        <h3>{el ? "Νέο μήνυμα" : "New message"}</h3>
        <button type="button" className="link" onClick={onClose}>{el ? "Άκυρο" : "Cancel"}</button>
      </div>
      <p className="muted small">
        {el ? "Κλήση ή ασύρματος που δέχτηκες: γράψε τι είπαν, με τα δικά τους λόγια όσο γίνεται. Περνά από τους ίδιους πράκτορες και περιμένει απόφαση."
          : "A call or radio message you took: write what they said, in their words where you can. It goes through the same agents and waits for a decision."}
      </p>
      <label className="small" htmlFor="nm-text">{el ? "Μήνυμα" : "Message"}</label>
      <textarea id="nm-text" rows={4} maxLength={2000} required value={text} onChange={(e) => setText(e.target.value)}
        placeholder={el ? "π.χ. «Είμαστε 3 στην Κυκλάδων στο Μάτι, η γιαγιά δεν περπατάει»" : "e.g. “3 of us on Kykladon, Mati; grandma can't walk”"} />
      <div className="row">
        <label className="small">{el ? "Κανάλι" : "Channel"}{" "}
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            {ENTRY_CHANNELS.map((c) => <option key={c} value={c}>{channelLabel(c, el ? "el" : "en")}</option>)}
          </select>
        </label>
        <label className="small">{el ? "Ελήφθη" : "Received"}{" "}
          <select value={ago} onChange={(e) => setAgo(Number(e.target.value))}>
            {[0, 2, 5, 10, 15, 30].map((m) => (
              <option key={m} value={m}>{m === 0 ? (el ? "τώρα" : "now") : `${m}′ ${el ? "πριν" : "ago"}`}</option>
            ))}
          </select>
          {received && <span className="muted"> {hhmm(received.toISOString(), tz)}</span>}
        </label>
      </div>
      <label className="small check">
        <input type="checkbox" checked={usePoint} disabled={!point} onChange={(e) => setUsePoint(e.target.checked)} />{" "}
        {point
          ? (el ? `Θέση από το σημείο που σημείωσες στον χάρτη (${point.lat.toFixed(4)}, ${point.lon.toFixed(4)})`
            : `Location from the point you marked (${point.lat.toFixed(4)}, ${point.lon.toFixed(4)})`)
          : (el ? "Σημείο στον χάρτη (προαιρετικό): κάνε κλικ στον χάρτη όπου είναι οι άνθρωποι"
            : "Point on the map (optional): click the map where the people are")}
      </label>
      {usePoint && <p className="muted small">{el ? "Το σημείο σου γίνεται η θέση· ο πράκτορας θέσης δεν την ξαναψάχνει." : "Your point becomes the location; the location agent does not search again."}</p>}
      <button className="primary" type="submit" disabled={submit.isPending || text.trim().length < 3}>
        {submit.isPending ? (el ? "Καταχώριση…" : "Entering…") : (el ? "Καταχώριση στην ουρά" : "Enter into the queue")}
      </button>
      {submit.error && <p className="notice">{(submit.error as Error).message}</p>}
    </form>
  );
}
