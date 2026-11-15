import { useEffect, useRef, useState } from "react";

import { useAlerts } from "../alerts/AlertDesk";
import { api, type ScenarioInfo } from "../api";
import { dict } from "../i18n";
import { useUi } from "../store";
import { fraction, hhmm } from "../time";

const SPEEDS = [1, 10, 60, 300];

export function TimeBar({ scenario }: { scenario: ScenarioInfo }) {
  const clock = useUi((s) => s.clock);
  const setClock = useUi((s) => s.setClock);
  const lang = useUi((s) => s.lang);
  const t = dict[lang];
  const tz = scenario.spec.timezone;
  const { start, end } = scenario.spec;
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();

  // While dragging, show the dragged time instead of the server's.
  const [drag, setDrag] = useState<number | null>(null);
  const seekTimer = useRef<number>();
  useEffect(() => () => window.clearTimeout(seekTimer.current), []);

  const now = drag !== null ? new Date(drag).toISOString() : clock?.sim_time ?? start;
  const anchors = [scenario.spec.fire.ignition, ...scenario.spec.fire.anchors];
  const sent = (useAlerts().data?.alerts ?? []).filter((a) => a.status === "sent" && a.acted_at);

  const seek = (ms: number) => {
    setDrag(ms);
    window.clearTimeout(seekTimer.current);
    seekTimer.current = window.setTimeout(async () => {
      setClock(await api.clock({ action: "seek", time: new Date(ms).toISOString() }));
      setDrag(null);
    }, 120);
  };

  return (
    <div className="timebar">
      <button
        className="play"
        onClick={async () => setClock(await api.clock({ action: clock?.playing ? "pause" : "play" }))}
        aria-label={clock?.playing ? t.pause : t.play}
        title={clock?.playing ? t.pause : t.play}
      >
        {clock?.playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      <output className="clock" aria-live="off">{hhmm(now, tz)}</output>
      <div className="speeds" role="group" aria-label={t.speed}>
        {SPEEDS.map((s) => (
          <button key={s} aria-pressed={clock?.speed === s}
            onClick={async () => setClock(await api.clock({ action: "speed", speed: s }))}>{s}×</button>
        ))}
      </div>
      <div className="track">
        <span className="track-end">{hhmm(start, tz)}</span>
        <div className="track-rail">
          <input
            type="range"
            min={startMs}
            max={endMs}
            step={60_000}
            value={new Date(now).getTime()}
            onChange={(e) => seek(Number(e.target.value))}
            aria-label={lang === "el" ? "Χρόνος σεναρίου" : "Scenario time"}
            aria-valuetext={hhmm(now, tz)}
          />
          {/* Milestones of the fire: a mark each, the label in the tooltip; a click jumps there. */}
          {anchors.map((a) => (
            <button
              key={a.id}
              className={`tick ${a.id === "ignition" ? "tick-ignition" : ""}`}
              style={{ left: `${fraction(a.time, start, end) * 100}%` }}
              onClick={() => seek(new Date(a.time).getTime())}
              title={`${hhmm(a.time, tz)} · ${a.label[lang]}`}
              aria-label={`${hhmm(a.time, tz)} · ${a.label[lang]}`}
              tabIndex={-1}
            />
          ))}
          {sent.map((a) => (
            <span key={a.id} className="alert-mark" style={{ left: `${fraction(a.acted_at!, start, end) * 100}%` }}
              title={`112 · ${hhmm(a.acted_at!, tz)} · ${a.settlement}`} />
          ))}
        </div>
        <span className="track-end">{hhmm(end, tz)}</span>
      </div>
    </div>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
      <path d="M7 4.5v15l12-7.5z" fill="currentColor" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
      <path d="M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z" fill="currentColor" />
    </svg>
  );
}
