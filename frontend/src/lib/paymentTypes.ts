// One label per payment type for the payment, rent account, open items, dunning and report pages,
// so the spelling is decided once. The bank assignment list keeps its own map, typed by the API's payment
// type, which also has NACHZAHLUNG; change a spelling there too.
export const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "Betriebskosten-Vorauszahlung",
  HK_VORAUSZAHLUNG: "Heizkosten-Vorauszahlung",
  KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss",
  RUECKLAGENANTEIL: "Rücklagenanteil",
  RUECKLAGENENTNAHME: "Rücklagenentnahme",
  SONDERZAHLUNG: "Sonder-/Nachzahlung",
};
