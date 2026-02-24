import React, { useEffect, useState } from 'react';
import type { LinkStateData } from '../../hooks/useLabStateWS';
import { getLinkDetail, type LinkPathDetail, type LinkEndpointDetail } from '../../../api';

interface VniLinkDetailPanelProps {
  labId: string;
  linkState: LinkStateData;
  onClose: () => void;
}

const STATE_DOT_COLORS: Record<string, string> = {
  up: 'bg-green-500',
  down: 'bg-stone-500',
  pending: 'bg-amber-500',
  error: 'bg-red-500',
  unknown: 'bg-stone-600',
  on: 'bg-green-500',
  off: 'bg-stone-500',
};

const STATE_TEXT_COLORS: Record<string, string> = {
  up: 'text-green-400',
  down: 'text-stone-500',
  pending: 'text-amber-400',
  error: 'text-red-400',
  unknown: 'text-stone-500',
  on: 'text-green-400',
  off: 'text-stone-500',
};

const TUNNEL_STATUS_COLORS: Record<string, string> = {
  active: 'text-green-400',
  pending: 'text-amber-400',
  failed: 'text-red-400',
  cleanup: 'text-stone-500',
};

function EndpointCard({ endpoint, label }: { endpoint: LinkEndpointDetail; label: string }) {
  const operDotColor = STATE_DOT_COLORS[endpoint.oper_state || 'unknown'] || STATE_DOT_COLORS.unknown;
  const operTextColor = STATE_TEXT_COLORS[endpoint.oper_state || 'unknown'] || STATE_TEXT_COLORS.unknown;
  const carrierDotColor = STATE_DOT_COLORS[endpoint.carrier_state || 'unknown'] || STATE_DOT_COLORS.unknown;
  const carrierTextColor = STATE_TEXT_COLORS[endpoint.carrier_state || 'unknown'] || STATE_TEXT_COLORS.unknown;

  return (
    <div className="flex-1 min-w-0">
      <div className="text-[10px] text-stone-500 uppercase font-bold tracking-wider mb-1.5">
        {label}
      </div>
      <div className="bg-stone-800/50 rounded-lg p-3 border border-stone-700/30">
        {/* Node name + host */}
        <div className="flex items-center gap-2 mb-2">
          <span className="text-sm font-medium text-stone-200">{endpoint.node_name}</span>
          {endpoint.host_name && (
            <span className="text-[10px] font-mono text-stone-500">
              ({endpoint.host_name})
            </span>
          )}
        </div>

        {/* Interface names */}
        <div className="space-y-1 text-xs">
          <div className="flex items-center gap-2">
            <span className="text-stone-500 w-16 flex-shrink-0">Interface</span>
            <span className="font-mono text-stone-300">
              {endpoint.vendor_interface || endpoint.interface}
            </span>
            {endpoint.vendor_interface && endpoint.vendor_interface !== endpoint.interface && (
              <span className="font-mono text-stone-500">
                ({endpoint.interface})
              </span>
            )}
          </div>

          {endpoint.ovs_port && (
            <div className="flex items-center gap-2">
              <span className="text-stone-500 w-16 flex-shrink-0">OVS port</span>
              <span className="font-mono text-stone-400 text-[10px]">{endpoint.ovs_port}</span>
            </div>
          )}

          {endpoint.vlan_tag != null && (
            <div className="flex items-center gap-2">
              <span className="text-stone-500 w-16 flex-shrink-0">VLAN</span>
              <span className="font-mono text-[10px] px-1.5 py-0.5 bg-stone-700/60 text-stone-300 rounded">
                {endpoint.vlan_tag}
              </span>
            </div>
          )}

          <div className="flex items-center gap-2">
            <span className="text-stone-500 w-16 flex-shrink-0">Carrier</span>
            <div className="flex items-center gap-1">
              <div className={`w-1.5 h-1.5 rounded-full ${carrierDotColor}`} />
              <span className={`font-medium ${carrierTextColor}`}>
                {endpoint.carrier_state || '-'}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-stone-500 w-16 flex-shrink-0">Oper</span>
            <div className="flex items-center gap-1">
              <div className={`w-1.5 h-1.5 rounded-full ${operDotColor}`} />
              <span className={`font-medium ${operTextColor}`}>
                {endpoint.oper_state || '-'}
              </span>
            </div>
            {endpoint.oper_reason && (
              <span className="text-stone-600 text-[10px]">({endpoint.oper_reason})</span>
            )}
          </div>

          {endpoint.vxlan_attached != null && (
            <div className="flex items-center gap-2">
              <span className="text-stone-500 w-16 flex-shrink-0">VXLAN</span>
              <span className={endpoint.vxlan_attached ? 'text-green-400' : 'text-stone-500'}>
                {endpoint.vxlan_attached ? 'attached' : 'detached'}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const VniLinkDetailPanel: React.FC<VniLinkDetailPanelProps> = ({
  labId,
  linkState,
  onClose,
}) => {
  const [detail, setDetail] = useState<LinkPathDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getLinkDetail(labId, linkState.link_name)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [labId, linkState.link_name]);

  const stateColor = STATE_TEXT_COLORS[linkState.actual_state] || STATE_TEXT_COLORS.unknown;
  const stateDotColor = STATE_DOT_COLORS[linkState.actual_state] || STATE_DOT_COLORS.unknown;

  return (
    <div className="border-t border-stone-700/50 bg-stone-900/90 backdrop-blur-xl overflow-hidden transition-all duration-300 ease-out"
      style={{ maxHeight: '45%' }}
    >
      <div className="flex flex-col h-full p-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-3 flex-shrink-0">
          <div className="flex items-center gap-3">
            <span className="text-xs text-stone-200 font-medium">
              {linkState.is_cross_host ? 'Cross-Host Link' : 'Link'} Detail
            </span>
            <div className="flex items-center gap-1">
              <div className={`w-1.5 h-1.5 rounded-full ${stateDotColor}`} />
              <span className={`text-xs font-medium ${stateColor}`}>{linkState.actual_state}</span>
            </div>
            {linkState.vni != null && (
              <span className="font-mono text-[10px] px-1.5 py-0.5 bg-violet-950/40 text-violet-400 rounded">
                VNI {linkState.vni}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-stone-500 hover:text-stone-300 transition-colors p-1"
            title="Close"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 min-h-0 overflow-auto">
          {loading && (
            <div className="flex items-center gap-2 text-xs text-stone-500 py-4">
              <div className="w-3 h-3 border border-stone-600 border-t-stone-400 rounded-full animate-spin" />
              Loading link detail...
            </div>
          )}

          {error && (
            <div className="text-xs text-red-400 py-2">
              {error}
            </div>
          )}

          {detail && !loading && (
            <div className="space-y-4">
              {/* Endpoint cards side by side */}
              <div className="flex gap-4">
                <EndpointCard endpoint={detail.source} label="Source" />
                <EndpointCard endpoint={detail.target} label="Target" />
              </div>

              {/* Tunnel section (only for cross-host) */}
              {detail.tunnel && (
                <div>
                  <div className="text-[10px] text-stone-500 uppercase font-bold tracking-wider mb-1.5">
                    VXLAN Tunnel
                  </div>
                  <div className="bg-stone-800/50 rounded-lg p-3 border border-stone-700/30">
                    <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="text-stone-500 w-16 flex-shrink-0">VNI</span>
                        <span className="font-mono text-violet-400">{detail.tunnel.vni}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-stone-500 w-16 flex-shrink-0">Status</span>
                        <span className={`font-medium ${TUNNEL_STATUS_COLORS[detail.tunnel.status] || 'text-stone-500'}`}>
                          {detail.tunnel.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-stone-500 w-16 flex-shrink-0">VLAN tag</span>
                        <span className="font-mono text-stone-300">{detail.tunnel.vlan_tag}</span>
                      </div>
                      {detail.tunnel.port_name && (
                        <div className="flex items-center gap-2">
                          <span className="text-stone-500 w-16 flex-shrink-0">Port</span>
                          <span className="font-mono text-stone-400 text-[10px]">{detail.tunnel.port_name}</span>
                        </div>
                      )}
                      <div className="col-span-2 flex items-center gap-2 mt-1">
                        <span className="text-stone-500 w-16 flex-shrink-0">Endpoints</span>
                        <span className="font-mono text-stone-300 text-[11px]">
                          {detail.tunnel.agent_a_ip}
                          <span className="text-stone-600 mx-1.5">&harr;</span>
                          {detail.tunnel.agent_b_ip}
                        </span>
                      </div>
                      {detail.tunnel.error_message && (
                        <div className="col-span-2 mt-1">
                          <span className="text-red-400 text-[10px]">{detail.tunnel.error_message}</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Link error message */}
              {detail.error_message && (
                <div className="bg-red-950/20 border border-red-900/30 rounded-lg px-3 py-2 text-xs text-red-400">
                  {detail.error_message}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default VniLinkDetailPanel;
