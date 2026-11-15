import { useUi } from "../store";

/** The drawer's title: the two kinds of agents, one tab each. */
export function AgentTabs() {
  const el = useUi((s) => s.lang) === "el";
  const tab = useUi((s) => s.flowTab);
  const setTab = useUi((s) => s.setFlowTab);
  const tabs: ["citizen" | "room", string][] = [
    ["citizen", el ? "Πολιτών" : "Citizens"],
    ["room", el ? "Αίθουσας επιχειρήσεων" : "Operations room"],
  ];
  return (
    <div className="ag-tabs" role="tablist" aria-label={el ? "Πράκτορες" : "Agents"}>
      <h2>{el ? "Πράκτορες" : "Agents"}</h2>
      {tabs.map(([id, label]) => (
        <button key={id} role="tab" aria-selected={tab === id} className={`ag-tab ${tab === id ? "is-on" : ""}`}
          onClick={() => setTab(id)}>{label}</button>
      ))}
    </div>
  );
}
