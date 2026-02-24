import React from 'react';
import { HostGroup } from './types';
import { LinkStateData } from '../../hooks/useLabStateWS';

interface InfraHeaderProps {
  hostGroups: HostGroup[];
  crossHostLinks: LinkStateData[];
  totalNodes: number;
  totalRunning: number;
  allVlanTags: Set<number>;
}

const InfraHeader: React.FC<InfraHeaderProps> = ({
  hostGroups,
  crossHostLinks,
  totalNodes,
  totalRunning,
  allVlanTags,
}) => {
  const totalLinks = crossHostLinks.length + hostGroups.reduce((sum, g) => sum + g.localLinks.length, 0);

  return (
    <div className="flex items-center gap-4 px-4 py-2.5 border-b border-stone-700/50 bg-stone-900/80 backdrop-blur flex-shrink-0">
      <StatBadge
        icon="fa-server"
        label="Hosts"
        value={hostGroups.length}
      />
      <StatBadge
        icon="fa-cube"
        label="Nodes"
        value={totalNodes}
        detail={`${totalRunning} running`}
      />
      <StatBadge
        icon="fa-link"
        label="Links"
        value={totalLinks}
        detail={crossHostLinks.length > 0 ? `${crossHostLinks.length} cross-host` : undefined}
      />
      {allVlanTags.size > 0 && (
        <StatBadge
          icon="fa-tags"
          label="VLANs"
          value={allVlanTags.size}
        />
      )}
    </div>
  );
};

const StatBadge: React.FC<{
  icon: string;
  label: string;
  value: number;
  detail?: string;
}> = ({ icon, label, value, detail }) => (
  <div className="flex items-center gap-1.5 text-xs text-stone-400">
    <i className={`fa-solid ${icon} text-stone-500`} />
    <span className="font-semibold text-stone-200">{value}</span>
    <span>{label}</span>
    {detail && <span className="text-stone-500">({detail})</span>}
  </div>
);

export default InfraHeader;
