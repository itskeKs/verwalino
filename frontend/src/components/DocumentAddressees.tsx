import type { DocumentAddressee } from "../api/types";
const germanDate = (value: string) => value.split("-").reverse().join(".");

export function DocumentAddressees({ addressees }: { addressees?: DocumentAddressee[] }) {
  if (!addressees?.length) return null;
  return <section className="card" aria-label="Dokumentempfänger" style={{ marginBottom: "1.5rem", overflowWrap: "anywhere" }}>
    <h2>Dokumentempfänger</h2>
    {addressees.map(a => <p key={`${a.unit_id}-${a.owner_profile_id}`}>
      <strong>Einheit {a.unit_number}: {a.owner_name}</strong><br />
      Eigentümer am {germanDate(a.key_date)}
    </p>)}
  </section>;
}
