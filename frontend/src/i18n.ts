import type { Lang } from "./api";

const el = {
  title: "Palladion",
  situation: "Κατάσταση",
  burned: "Καμένη έκταση",
  head: "Απόσταση μετώπου από την έναρξη",
  buildingsReached: "Κτίρια που έφτασε το μέτωπο",
  legendPerimeter: "Περίμετρος Copernicus (παρατήρηση)",
  wind: "Άνεμος",
  windFrom: (dir: string, min: number, max: number, gust: number | null) =>
    `${dir}, ${min}–${max} km/h${gust ? `, ριπές έως ${gust}` : ""}`,
  notStarted: "Η φωτιά δεν έχει ξεκινήσει ακόμα.",
  pointTitle: "Σημείο στον χάρτη",
  pointHint: "Πάτα σε ένα σημείο του χάρτη για να δεις πότε φτάνει εκεί το μέτωπο.",
  arrivesAt: "Το μέτωπο φτάνει",
  arrivesIn: (m: number) => `σε ${formatMinutes(m)}`,
  arrivedAgo: (m: number) => `πριν από ${formatMinutes(-m)}`,
  never: "Το ανασυνθεμένο μέτωπο δεν φτάνει εδώ.",
  routeButton: "Βρες διαδρομή εκκένωσης",
  routing: "Υπολογισμός…",
  noRoads: "Δεν έχουν φορτωθεί δρόμοι. Τρέξε το data pipeline για να ενεργοποιηθεί η δρομολόγηση.",
  route: {
    ok: "Ασφαλής διαδρομή",
    no_safe_route: "Καμία ασφαλής διαδρομή με όχημα",
    origin_burned: "Το μέτωπο έχει ήδη περάσει",
    off_network: "Εκτός οδικού δικτύου",
  },
  to: "Προς",
  arrive: "Άφιξη",
  distance: "Απόσταση",
  margin: "Ελάχιστο περιθώριο από το μέτωπο",
  notThreatened: "δεν απειλείται",
  legend: "Υπόμνημα",
  legendBurned: "Καμένη έκταση",
  legendFront: "Μέτωπο",
  legendIso: "Ισόχρονες ανά 15 λεπτά",
  legendThreat: "Δρόμος που κλείνει σε ≤ 30 λεπτά",
  legendClosed: "Δρόμος που έχει καεί",
  legendDeadEnd: "Αδιέξοδο",
  legendBuilding: "Κτίριο που έφτασε το μέτωπο",
  legendZone: "Ζώνη καταφυγής",
  legendCliff: "Γκρεμός στην ακτή",
  legendAccess: "Ακτή με πρόσβαση στο νερό",
  legendAnchor: "Σημείο με πηγή",
  legendCase: "Περιστατικό (μέγεθος: άτομα)",
  legendCaseUrgent: "Το μέτωπο σε ≤ 10′",
  legendCaseOverdue: "Το μέτωπο έφτασε",
  legendCaseHandled: "Αποφασίστηκε",
  legendUnit: "Μονάδα (κενός κύκλος: εκτίμηση θέσης)",
  legendUnitRoute: "Διαδρομή μονάδας",
  legendPlanRoute: "Προτεινόμενη διαδρομή μονάδας",
  legendAlert: "Περιοχή 112 (διακεκομμένη: πρόταση)",
  legendRoute: "Διαδρομή εκκένωσης",
  play: "Αναπαραγωγή",
  pause: "Παύση",
  speed: "Ταχύτητα",
  data: "Δεδομένα",
  dataOsm: "OpenStreetMap",
  dataFallback: "πρόχειρα",
  sources: "Πηγές",
  confidence: { documented: "τεκμηριωμένο", derived: "υπολογισμένο", approximate: "κατά προσέγγιση" },
  close: "Κλείσιμο",
};

type Dict = typeof el;

const en: Dict = {
  ...el,
  situation: "Situation",
  burned: "Burned area",
  head: "Front distance from ignition",
  buildingsReached: "Buildings the front reached",
  legendPerimeter: "Copernicus perimeter (observed)",
  wind: "Wind",
  windFrom: (dir, min, max, gust) => `${dir}, ${min}–${max} km/h${gust ? `, gusts to ${gust}` : ""}`,
  notStarted: "The fire has not started yet.",
  pointTitle: "Map point",
  pointHint: "Click a point on the map to see when the front reaches it.",
  arrivesAt: "Front arrives",
  arrivesIn: (m) => `in ${formatMinutes(m, "en")}`,
  arrivedAgo: (m) => `${formatMinutes(-m, "en")} ago`,
  never: "The reconstructed front never reaches this point.",
  routeButton: "Find evacuation route",
  routing: "Calculating…",
  noRoads: "No roads loaded. Run the data pipeline to enable routing.",
  route: {
    ok: "Safe route",
    no_safe_route: "No safe route by vehicle",
    origin_burned: "The front has already passed",
    off_network: "Off the road network",
  },
  to: "To",
  arrive: "Arrival",
  distance: "Distance",
  margin: "Smallest margin ahead of the front",
  notThreatened: "not threatened",
  legend: "Legend",
  legendBurned: "Burned area",
  legendFront: "Front",
  legendIso: "Isochrones every 15 minutes",
  legendThreat: "Road closing in ≤ 30 minutes",
  legendClosed: "Burned road",
  legendDeadEnd: "Dead end",
  legendBuilding: "Building the front reached",
  legendZone: "Refuge zone",
  legendCliff: "Cliff at the shore",
  legendAccess: "Shore with access to the water",
  legendAnchor: "Sourced point",
  legendCase: "Case (size: people)",
  legendCaseUrgent: "Front ≤ 10′ away",
  legendCaseOverdue: "Front has arrived",
  legendCaseHandled: "Decided",
  legendUnit: "Unit (hollow: estimated position)",
  legendUnitRoute: "Unit route",
  legendPlanRoute: "Proposed unit route",
  legendAlert: "112 area (dashed: proposed)",
  legendRoute: "Evacuation route",
  play: "Play",
  pause: "Pause",
  speed: "Speed",
  data: "Data",
  dataFallback: "rough",
  sources: "Sources",
  confidence: { documented: "documented", derived: "derived", approximate: "approximate" },
  close: "Close",
};

export const dict: Record<Lang, Dict> = { el, en };

export function formatMinutes(m: number, lang: Lang = "el"): string {
  const total = Math.round(m);
  const h = Math.floor(total / 60);
  const min = total % 60;
  const minWord = lang === "el" ? "λεπτά" : "min";
  if (h === 0) return `${min} ${minWord}`;
  return `${h} ${lang === "el" ? "ώ" : "h"} ${min} ${minWord}`;
}

const COMPASS_EL = ["Β", "ΒΒΑ", "ΒΑ", "ΑΒΑ", "Α", "ΑΝΑ", "ΝΑ", "ΝΝΑ", "Ν", "ΝΝΔ", "ΝΔ", "ΔΝΔ", "Δ", "ΔΒΔ", "ΒΔ", "ΒΒΔ"];
const COMPASS_EN = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

export function compass(bearing: number, lang: Lang): string {
  const i = Math.round((((bearing % 360) + 360) % 360) / 22.5) % 16;
  return (lang === "el" ? COMPASS_EL : COMPASS_EN)[i];
}
