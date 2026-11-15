import type { Lang } from "../api";

export const DECISIONS = [
  "EVACUATE_ROUTE_CANDIDATE",
  "DISPATCH_UNIT_CANDIDATE",
  "SHELTER_CANDIDATE",
  "SEA_RESCUE_CANDIDATE",
  "CALLBACK",
  "MISSING_PERSON",
  "REPORTED_SAFE",
  "MONITOR",
  "INFO",
  "REVIEW_REQUIRED",
  "OUT_OF_AREA",
] as const;

const DECISION: Record<Lang, Record<string, string>> = {
  el: {
    EVACUATE_ROUTE_CANDIDATE: "Εκκένωση με διαδρομή",
    DISPATCH_UNIT_CANDIDATE: "Αποστολή μονάδας",
    SHELTER_CANDIDATE: "Προστασία επί τόπου",
    SEA_RESCUE_CANDIDATE: "Θαλάσσια διάσωση",
    CALLBACK: "Επανάκληση",
    MISSING_PERSON: "Αγνοούμενος",
    REPORTED_SAFE: "Ασφαλείς",
    MONITOR: "Επιφυλακή",
    INFO: "Πληροφορία",
    REVIEW_REQUIRED: "Έλεγχος από άνθρωπο",
    OUT_OF_AREA: "Εκτός περιοχής",
  },
  en: {
    EVACUATE_ROUTE_CANDIDATE: "Evacuate by route",
    DISPATCH_UNIT_CANDIDATE: "Send a unit",
    SHELTER_CANDIDATE: "Shelter in place",
    SEA_RESCUE_CANDIDATE: "Sea rescue",
    CALLBACK: "Call back",
    MISSING_PERSON: "Missing person",
    REPORTED_SAFE: "Safe",
    MONITOR: "Stand by",
    INFO: "Information",
    REVIEW_REQUIRED: "Human review",
    OUT_OF_AREA: "Out of area",
  },
};

export function decisionLabel(d: string | null, lang: Lang): string {
  if (!d) return "—";
  return DECISION[lang][d] ?? d;
}

const CHANNEL: Record<string, [string, string]> = {
  "199_call": ["199 κλήση", "199 call"],
  "199_sms": ["199 SMS", "199 SMS"],
  "100_call": ["100 κλήση", "100 call"],
  "166_call": ["166 κλήση", "166 call"],
  "112_sms": ["112 SMS", "112 SMS"],
  sms: ["SMS", "SMS"],
  facebook: ["Facebook", "Facebook"],
  messenger: ["Messenger", "Messenger"],
  twitter: ["Twitter", "Twitter"],
  radio: ["Ασύρματος", "Radio"],
  field: ["Από το πεδίο", "From the field"],
  social: ["Μέσα κοινωνικής δικτύωσης", "Social media"],
  live_web: ["Σελίδα πολιτών", "Public page"],
};
/** Channels an operator can enter a message from (backend: CaseService.CHANNELS). */
export const ENTRY_CHANNELS = ["199_call", "166_call", "100_call", "112_sms", "radio", "field", "social"] as const;
export const channelLabel = (c: string, lang: Lang = "el") => CHANNEL[c]?.[lang === "el" ? 0 : 1] ?? c;

const FLAG: Record<Lang, Record<string, string>> = {
  el: {
    injection: "Προσπάθεια εντολής προς το σύστημα",
    rumor: "Ανεπιβεβαίωτο",
    contradicts_model: "Η αναφορά διαφωνεί με το μοντέλο φωτιάς",
    upgraded_from_info: "Αναβαθμίστηκε σε αίτημα (φωτιά κοντά)",
    missing_at_known_address: "Αγνοούμενος σε γνωστή διεύθυνση",
    area_inferred: "Η περιοχή συμπεράνθηκε· θέλει επιβεβαίωση",
    location_disputed: "Ο πράκτορας θέσης διαφωνεί με τη θέση· δες την πρότασή του",
    agent_failed: "Ο πράκτορας απέτυχε",
    cliff_nearby: "Γκρεμός στην ακτή κοντά: πρόσβαση ίσως μόνο από θάλασσα",
    critic_revised: "Ο Ελεγκτής διόρθωσε την ανάγνωση",
    hazard_changed: "Ο κριτής κινδύνου άλλαξε την πρόταση",
    credibility_doubtful: "Αξιοπιστία: θέλει επιβεβαίωση",
    credibility_likely_false: "Αξιοπιστία: πιθανώς ψευδές",
    hazard_cautions: "Προειδοποιήσεις κινδύνου",
    hazard_check_failed: "Ο έλεγχος κινδύνου δεν έτρεξε",
    operator_point: "Θέση από σημείο του χειριστή στον χάρτη",
    either_or: "Δεν ξέρουν σε ποιο από δύο σημεία είναι",
    pii_masked: "Προσωπικά στοιχεία κρύφτηκαν από τους πράκτορες",
    people_derived: "Ο αριθμός ατόμων μετρήθηκε από την περιγραφή· επιβεβαίωσέ τον",
    arbiter_changed: "Ο διαιτητής άλλαξε την πρόταση",
  },
  en: {
    injection: "Tries to instruct the system",
    rumor: "Unverified",
    contradicts_model: "Report disagrees with the fire model",
    upgraded_from_info: "Upgraded to a request (fire nearby)",
    missing_at_known_address: "Missing at a known address",
    area_inferred: "Area inferred; needs confirmation",
    location_disputed: "The location agent disagrees with the place; see its proposal",
    agent_failed: "Agent failed",
    cliff_nearby: "Cliff at the shore nearby: access may be from the sea only",
    critic_revised: "The critic corrected the reading",
    hazard_changed: "The hazard critic changed the proposal",
    credibility_doubtful: "Credibility: needs checking",
    credibility_likely_false: "Credibility: likely false",
    hazard_cautions: "Hazard cautions",
    hazard_check_failed: "The hazard check did not run",
    operator_point: "Location from the operator's point on the map",
    either_or: "They do not know which of two places they are at",
    pii_masked: "Personal data masked from the agents",
    people_derived: "People counted from the description; confirm",
    arbiter_changed: "The arbiter changed the proposal",
  },
};
export const flagLabel = (f: string, lang: Lang) => FLAG[lang][f] ?? f;

export function countdown(m: number | null, lang: Lang): string {
  if (m === null) return "—";
  if (m <= 0) return lang === "el" ? "πέρασε" : "passed";
  if (m < 60) return `${Math.round(m)}′`;
  return `${Math.floor(m / 60)}ω${String(Math.round(m % 60)).padStart(2, "0")}`;
}

const VULNERABLE: Record<Lang, Record<string, string>> = {
  el: {
    elderly: "ηλικιωμένοι", child: "παιδιά", mobility: "κινητικά προβλήματα", bedridden: "κατάκοιτος",
    pregnant: "έγκυος", respiratory: "αναπνευστικό", burns: "εγκαύματα", injury: "τραυματισμός",
    no_vehicle: "χωρίς όχημα", animals: "ζώα",
  },
  en: {
    elderly: "elderly", child: "children", mobility: "mobility", bedridden: "bedridden", pregnant: "pregnant",
    respiratory: "breathing", burns: "burns", injury: "injury", no_vehicle: "no vehicle", animals: "animals",
  },
};
export const vulnerableLabel = (v: string, lang: Lang) => VULNERABLE[lang][v] ?? v;
