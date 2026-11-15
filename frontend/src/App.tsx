import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api, setApiLang } from "./api";
import { MapView } from "./map/MapView";
import { AgentFlow } from "./flow/AgentFlow";
import { RoomAgents } from "./flow/RoomAgents";
import { SidePanel } from "./panel/SidePanel";
import { Spotlight } from "./send/Spotlight";
import { useUi } from "./store";
import { TimeBar } from "./timebar/TimeBar";
import { useClockStream } from "./useClock";

export function App() {
  useClockStream();
  const lang = useUi((s) => s.lang);
  const setLang = useUi((s) => s.setLang);
  setApiLang(lang);                              // before any fetch of this render: code's sentences in this language
  const qc = useQueryClient();
  const first = useRef(true);
  useEffect(() => {                              // switching language: read everything again
    if (first.current) { first.current = false; return; }
    void qc.invalidateQueries();
  }, [lang]);
  const panelOpen = useUi((s) => s.panelOpen);
  const caseOpen = useUi((s) => s.tab === "cases" && !!s.caseId);
  const setPanelOpen = useUi((s) => s.setPanelOpen);
  const flowOpen = useUi((s) => s.flowOpen);
  const setFlowOpen = useUi((s) => s.setFlowOpen);
  const flowTab = useUi((s) => s.flowTab);
  const scenario = useQuery({ queryKey: ["scenario"], queryFn: api.scenario });
  const el = lang === "el";

  if (scenario.isError) {
    return (
      <main className="boot">
        <h1>Palladion</h1>
        <p>
          {el
            ? "Το API δεν απαντά. Ξεκίνα τον server (README, βήμα 4) και ανανέωσε τη σελίδα."
            : "The API is not answering. Start the server (README, step 4) and reload."}
        </p>
      </main>
    );
  }
  if (!scenario.data) return <main className="boot" aria-busy="true" />;
  const s = scenario.data;

  return (
    <div className={`shell ${panelOpen ? "" : "panel-closed"} ${caseOpen ? "case-open" : ""}`}>
      <header className="topbar">
        <div className="brand-block">
          <h1 className="brand">Palladion</h1>
          <p className="scenario-name">{s.spec.name[lang]}</p>
        </div>
        <TimeBar scenario={s} />
        <div className="top-right">
          <button className={`flow-btn ${flowOpen ? "is-on" : ""}`} aria-pressed={flowOpen} onClick={() => setFlowOpen(!flowOpen)}
            title={el ? "Τι κάνει κάθε πράκτορας, ζωντανά, με τους χρόνους" : "What each agent does, live, with timings"}>
            {el ? "Πράκτορες" : "Agents"}
          </button>
          <div className="lang" role="group" aria-label="Γλώσσα / Language">
            {(["el", "en"] as const).map((l) => (
              <button key={l} aria-pressed={lang === l} onClick={() => setLang(l)}>{l === "el" ? "ΕΛ" : "EN"}</button>
            ))}
          </div>
        </div>
      </header>
      <MapView scenario={s} />
      <Spotlight tz={s.spec.timezone} />
      {flowOpen && flowTab === "citizen" && <AgentFlow tz={s.spec.timezone} />}
      {flowOpen && flowTab === "room" && <RoomAgents tz={s.spec.timezone} />}
      <button className="panel-toggle" onClick={() => setPanelOpen(!panelOpen)}
        aria-expanded={panelOpen} aria-label={panelOpen ? (el ? "Κλείσιμο πλαισίου" : "Close the panel") : (el ? "Άνοιγμα πλαισίου" : "Open the panel")}
        title={panelOpen ? (el ? "Κλείσιμο πλαισίου: ο χάρτης σε όλο το πλάτος" : "Close the panel: full-width map") : (el ? "Άνοιγμα πλαισίου" : "Open the panel")}>
        {panelOpen ? "›" : "‹"}
      </button>
      {panelOpen && <SidePanel scenario={s} />}
    </div>
  );
}

