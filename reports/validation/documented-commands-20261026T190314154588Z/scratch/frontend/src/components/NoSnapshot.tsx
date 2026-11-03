import { CloudOff } from 'lucide-react';
import { EmptyState } from './ui';

export function NoSnapshot({ error }: { error?: string }) {
  return (
    <EmptyState
      icon={CloudOff}
      title="Facility snapshot unavailable"
      description={error ?? 'No facility snapshot has been received. Aegis will not substitute simulated cameras, people, or measurements.'}
    />
  );
}
