import { useState } from 'react';
import { Link } from 'react-router-dom';
import { SearchChoice } from '../components/SearchChoice';
import { VisualDisclosureList } from '../components/VisualDisclosureList';
import type { DisclosureScope } from '../api/visualDisclosures';

export function VisualDisclosuresPage() {
  const [outgoing, setOutgoing] = useState(false), [pending, setPending] = useState(false);
  const [scope, setScope] = useState<DisclosureScope | null>(null);
  const endpoint = outgoing ? '/api/visual-disclosures/outgoing' : scope ? `/api/accounting-scopes/${scope.id}/visual-disclosures` : null;
  return <div className="space-y-5">
    <Link className="underline" to="/belege">Zu den Belegen</Link>
    <h1>Freigegebene Seitenauszüge</h1>
    <p>Hier finden Sie ausdrücklich freigegebene sichtbare Seiten. Die Freigabe gewährt keinen Zugriff auf das Original und bestätigt keine Kosten.</p>
    <fieldset disabled={pending} className="flex flex-wrap gap-4">
      <label className="flex gap-2"><input type="radio" name="disclosure-audience" checked={!outgoing} onChange={() => setOutgoing(false)} />Auszüge im Zielbereich</label>
      <label className="flex gap-2"><input type="radio" name="disclosure-audience" checked={outgoing} onChange={() => setOutgoing(true)} />Meine ausgehenden Freigaben</label>
      {!outgoing && <SearchChoice endpoint="/api/accounting-scopes/page?capability=read" value={scope} onChange={setScope} label="Zielbereich" display={item => `${item.label} · ${item.scope_type}`} />}
    </fieldset>
    {outgoing && <p>Eigene Freigaben können Sie auch dann widerrufen, wenn Sie den Zielbereich nicht mehr lesen können oder die Quelle erneut geprüft wird.</p>}
    {endpoint ? <VisualDisclosureList key={endpoint} endpoint={endpoint} onPendingChange={setPending} /> : <p>Wählen Sie einen Bereich mit Leseberechtigung.</p>}
  </div>;
}
