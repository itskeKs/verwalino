import { describe, expect, it } from "vitest";

// Issue #50: visible German copy uses umlauts and ß. A transliterated spelling ("Flaeche",
// "waehlen", "Eigentuemer") reads as a defect to a user (DESIGN.md "Tone and wording"). This scans
// the source of every page and component for the spellings that occurred; enum values such as
// `UMLAGEFAEHIG`, URL paths and template placeholders keep their ASCII form and are not matched.
const sources = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
});

const transliterated = new RegExp(
  [
    "Eigentuemer", "koennen", "Flaeche", "Ruecklage", "Durchfuehrung", "Durchgefuehrt", "durchgefuehrt",
    "unveraender", "moeglich", "Beschluesse", "geaendert", "aenderbar", "Aenderung", "geloescht", "loeschen",
    "hinzufuegen", "hinzugefuegt", "[Uu]ebernommen", "[Uu]ebernehm", "Uebersicht", "Ueberschneidung", "\\bFuer\\b",
    "[Ww]aehlen", "Empfaenger", "Groesse", "Verknuepf", "Pruefung", "\\bpruefen\\b", "Integritaet", "noetig",
    "[Bb]estaetigt", "[Uu]mlagefaeh", "Mietvertraege", "Betraege", "Rueckstand", "Eintraege", "Umlageschluessel",
    "Ausserordentlich", "schliessen", "Strasse", "\\bfuer\\b", "Loeschen", "Gebuehr", "zusaetzlich", "fuehren",
    "Aendern", "Vorgaengerversion", "ergaenzen", "ueberfaellig",
  ].join("|"),
  "g",
);

// Files owned by #495 (property, unit, person and cost screens): its sweep removes these entries.
const owned = [
  "/costVocabulary.ts", "/CostEntriesPage.tsx", "/CostEntryDetailPage.tsx", "/PropertyDetailPage.tsx",
  "/PropertiesPage.tsx", "/UnitDetailPage.tsx", "/PersonDetailPage.tsx", "/PersonsPage.tsx",
];

// Comments are not visible; blank them but keep the line numbers.
function withoutComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

describe("visible German copy", () => {
  it("is written with umlauts and ß, not transliterated", () => {
    const found: string[] = [];
    for (const [path, text] of Object.entries(sources)) {
      if (owned.some((file) => path.endsWith(file))) continue;
      withoutComments(text).split("\n").forEach((line, index) => {
        const hits = line.match(transliterated);
        if (hits) found.push(`${path.replace("../", "src/")}:${index + 1} ${[...new Set(hits)].join(", ")}`);
      });
    }
    expect(found).toEqual([]);
  });
});
