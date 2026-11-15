import { useEffect, useRef, useState } from "react";

export const UNDO_S = 10;

/** A send that waits 10 seconds and can be undone in that time. If the card closes first, nothing is sent:
 *  the safe default is "not sent", never "sent without being seen". */
export function useDelayedSend() {
  const [pending, setPending] = useState<{ label: string; left: number } | null>(null);
  const timer = useRef<number>();
  const tick = useRef<number>();
  const clear = () => { window.clearTimeout(timer.current); window.clearInterval(tick.current); };
  useEffect(() => clear, []);
  const start = (label: string, send: () => void) => {
    clear();
    setPending({ label, left: UNDO_S });
    tick.current = window.setInterval(() => setPending((p) => (p ? { ...p, left: Math.max(0, p.left - 1) } : p)), 1000);
    timer.current = window.setTimeout(() => { clear(); setPending(null); send(); }, UNDO_S * 1000);
  };
  const undo = () => { clear(); setPending(null); };
  return { pending, start, undo };
}
