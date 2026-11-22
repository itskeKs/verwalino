import { useCallback } from 'react';
import { extractionApi } from '../../api/intake';
import { ModelUsage } from '../InvoiceModelUsage';

/** What the models have cost for one document of the inbox so far, over all its attempts; the amounts are read again when its state changes. */
export function IntakeModelUsage({ logicalId, state }: { logicalId: string; state: string }) {
  const load = useCallback(() => extractionApi.usage(logicalId), [logicalId]);
  return <ModelUsage load={load} reloadKey={state} />;
}
