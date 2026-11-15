import { create } from "zustand";

import type { ClockState, Lang, RouteResult } from "./api";

export type Tab = "cases" | "alerts" | "supervisor" | "situation";

interface UiState {
  lang: Lang;
  clock: ClockState | null;
  point: { lon: number; lat: number } | null;
  route: RouteResult | null;
  tab: Tab;
  setTab: (t: Tab) => void;
  alertId: string | null;
  setAlertId: (id: string | null) => void;
  caseId: string | null;
  setCaseId: (id: string | null) => void;
  setLang: (l: Lang) => void;
  setClock: (c: ClockState) => void;
  setPoint: (p: { lon: number; lat: number } | null) => void;
  setRoute: (r: RouteResult | null) => void;
  planRoute: [number, number][] | null;           // the proposed route of a land unit for the open case
  setPlanRoute: (r: [number, number][] | null) => void;
  flowOpen: boolean;                               // the agents drawer over the map
  setFlowOpen: (v: boolean) => void;
  flowTab: "citizen" | "room";                     // inside it: the citizens' agents or the operations room's
  setFlowTab: (v: "citizen" | "room") => void;
  panelOpen: boolean;                              // the side panel; closed, the map takes the whole width
  setPanelOpen: (v: boolean) => void;
}

export const useUi = create<UiState>((set) => ({
  lang: "el",
  clock: null,
  point: null,
  route: null,
  tab: "cases",
  setTab: (tab) => set({ tab }),
  alertId: null,
  setAlertId: (alertId) => set(alertId ? { alertId, tab: "alerts" } : { alertId }),
  caseId: null,
  setCaseId: (caseId) => set(caseId ? { caseId, tab: "cases" } : { caseId }),
  setLang: (lang) => set({ lang }),
  setClock: (clock) => set({ clock }),
  setPoint: (point) => set({ point, route: null }),
  setRoute: (route) => set({ route }),
  planRoute: null,
  setPlanRoute: (planRoute) => set({ planRoute }),
  flowOpen: false,
  setFlowOpen: (flowOpen) => set({ flowOpen }),
  flowTab: "citizen",
  setFlowTab: (flowTab) => set({ flowTab }),
  panelOpen: true,
  setPanelOpen: (panelOpen) => set({ panelOpen }),
}));
