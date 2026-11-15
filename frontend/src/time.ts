const fmt = new Map<string, Intl.DateTimeFormat>();

/** HH:MM in the scenario's time zone, whatever the viewer's own zone is. */
export function hhmm(iso: string, timeZone = "Europe/Athens"): string {
  let f = fmt.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat("el-GR", { timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    fmt.set(timeZone, f);
  }
  return f.format(new Date(iso));
}

export function minutesBetween(a: string, b: string): number {
  return (new Date(b).getTime() - new Date(a).getTime()) / 60000;
}

/** Fraction of [start, end] at which t falls, clamped to [0, 1]. */
export function fraction(t: string, start: string, end: string): number {
  const s = new Date(start).getTime();
  const e = new Date(end).getTime();
  return Math.min(1, Math.max(0, (new Date(t).getTime() - s) / (e - s)));
}
