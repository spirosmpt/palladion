import { useEffect } from "react";

import type { ClockState } from "./api";
import { useUi } from "./store";

/** Follows the server's scenario clock over Server-Sent Events, reconnecting on failure. */
export function useClockStream(): void {
  const setClock = useUi((s) => s.setClock);
  useEffect(() => {
    let es: EventSource | null = null;
    let retry: number | undefined;
    const connect = () => {
      es = new EventSource("/api/stream");
      es.addEventListener("clock", (ev) => setClock(JSON.parse((ev as MessageEvent).data) as ClockState));
      es.onerror = () => {
        es?.close();
        retry = window.setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      es?.close();
      window.clearTimeout(retry);
    };
  }, [setClock]);
}
