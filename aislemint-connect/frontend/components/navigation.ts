import {
  Activity,
  GitBranch,
  Layers,
  Link2,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';

/** Shared by the workspace and platform shell so navigation has one owner. */
export const connectedAreas = [
  { name: 'Operating picture', path: '/connect', icon: Activity },
  { name: 'Connections', path: '/connect/connections', icon: Link2 },
  { name: 'Ontology', path: '/connect/ontology', icon: Layers },
  { name: 'Pipelines', path: '/connect/pipelines', icon: GitBranch },
  { name: 'Decisions', path: '/connect/decisions', icon: ShieldCheck },
  { name: 'Evidence assistant', path: '/connect/assistant', icon: Sparkles },
] as const;

export type ConnectedArea = (typeof connectedAreas)[number]['name'];
