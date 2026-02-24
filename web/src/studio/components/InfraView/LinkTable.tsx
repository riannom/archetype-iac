import React, { useMemo, useState, useCallback, useRef, type ReactNode } from 'react';
import type { HostGroup } from './types';
import type { LinkStateData } from '../../hooks/useLabStateWS';
import { usePersistedState } from '../../hooks/usePersistedState';
import { getAgentColor } from '../../../utils/agentColors';

interface LinkTableProps {
  hostGroups: HostGroup[];
  crossHostLinks: LinkStateData[];
  vendorLookup: Map<string, string>;
  selectedLinkName: string | null;
  onSelectLink: (linkName: string) => void;
}

const STATE_DOT_COLORS: Record<string, string> = {
  up: 'bg-green-500',
  down: 'bg-stone-500',
  pending: 'bg-amber-500',
  error: 'bg-red-500',
  unknown: 'bg-stone-600',
};

const STATE_TEXT_COLORS: Record<string, string> = {
  up: 'text-green-400',
  down: 'text-stone-500',
  pending: 'text-amber-400',
  error: 'text-red-400',
  unknown: 'text-stone-500',
};

const ROW_TINTS: Record<string, string> = {
  up: 'bg-green-950/20',
  error: 'bg-red-950/20',
  pending: 'bg-amber-950/15',
};

const STATE_ORDER: Record<string, number> = {
  error: 0,
  pending: 1,
  unknown: 2,
  down: 3,
  up: 4,
};

// ─── Column Definition Types ──────────────────────────────────────

type Endpoint = { node: string; iface: string };

type ColumnDef = {
  id: string;
  header: string;
  align: 'left' | 'center';
  render: (
    ls: LinkStateData,
    src: Endpoint,
    tgt: Endpoint,
    vendorLookup: Map<string, string>,
  ) => ReactNode;
};

// ─── Helpers ──────────────────────────────────────────────────────

/** Parse "node:iface" from a link_name segment. */
function parseEndpoint(part: string): Endpoint {
  const colonIdx = part.indexOf(':');
  if (colonIdx < 0) return { node: part, iface: '' };
  return { node: part.slice(0, colonIdx), iface: part.slice(colonIdx + 1) };
}

/** Format interface display: "Ethernet1 (eth1)" or just "eth1" if no vendor mapping. */
function formatIface(containerName: string, linuxIface: string, vendorLookup: Map<string, string>): string {
  const vendor = vendorLookup.get(`${containerName}:${linuxIface}`);
  if (vendor && vendor !== linuxIface) return `${vendor} (${linuxIface})`;
  return linuxIface;
}

function sortLinks(links: LinkStateData[]): LinkStateData[] {
  return [...links].sort((a, b) => {
    const ao = STATE_ORDER[a.actual_state] ?? 5;
    const bo = STATE_ORDER[b.actual_state] ?? 5;
    if (ao !== bo) return ao - bo;
    return a.link_name.localeCompare(b.link_name);
  });
}

/** Parse a link_name into source + target endpoints. */
function parseLinkEndpoints(linkName: string): [Endpoint, Endpoint] {
  const dashIdx = linkName.indexOf('-');
  const srcPart = dashIdx >= 0 ? linkName.slice(0, dashIdx) : linkName;
  const tgtPart = dashIdx >= 0 ? linkName.slice(dashIdx + 1) : '';
  return [parseEndpoint(srcPart), parseEndpoint(tgtPart)];
}

// ─── Column Definitions ───────────────────────────────────────────

// Shared renderers
function renderState(ls: LinkStateData): ReactNode {
  const dotColor = STATE_DOT_COLORS[ls.actual_state] || STATE_DOT_COLORS.unknown;
  const textColor = STATE_TEXT_COLORS[ls.actual_state] || STATE_TEXT_COLORS.unknown;
  return (
    <div className="flex items-center justify-center gap-1">
      <div className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
      <span className={`font-medium ${textColor}`}>{ls.actual_state}</span>
    </div>
  );
}

function renderError(ls: LinkStateData): ReactNode {
  return ls.error_message ? (
    <span className="text-red-400 truncate block" title={ls.error_message}>
      {ls.error_message}
    </span>
  ) : (
    <span className="text-stone-600">-</span>
  );
}

function renderVlanBadge(value: number | null | undefined): ReactNode {
  return value != null ? (
    <span className="font-mono text-[10px] px-1 py-0.5 bg-stone-800 text-stone-400 rounded">
      {value}
    </span>
  ) : (
    <span className="text-stone-600">-</span>
  );
}

const LOCAL_COLUMNS: ColumnDef[] = [
  {
    id: 'state',
    header: 'State',
    align: 'center',
    render: (ls) => renderState(ls),
  },
  {
    id: 'source',
    header: 'Source',
    align: 'left',
    render: (_ls, src) => (
      <span className="font-mono text-stone-300 whitespace-nowrap">{src.node}</span>
    ),
  },
  {
    id: 'srcIface',
    header: 'Src Interface',
    align: 'left',
    render: (ls, src, _tgt, vl) => (
      <span className="font-mono text-stone-400 whitespace-nowrap text-[11px]">
        {formatIface(ls.source_node, src.iface, vl)}
      </span>
    ),
  },
  {
    id: 'target',
    header: 'Target',
    align: 'left',
    render: (_ls, _src, tgt) => (
      <span className="font-mono text-stone-300 whitespace-nowrap">{tgt.node}</span>
    ),
  },
  {
    id: 'tgtIface',
    header: 'Tgt Interface',
    align: 'left',
    render: (ls, _src, tgt, vl) => (
      <span className="font-mono text-stone-400 whitespace-nowrap text-[11px]">
        {formatIface(ls.target_node, tgt.iface, vl)}
      </span>
    ),
  },
  {
    id: 'error',
    header: 'Error',
    align: 'left',
    render: (ls) => renderError(ls),
  },
];

const CROSS_HOST_COLUMNS: ColumnDef[] = [
  {
    id: 'state',
    header: 'State',
    align: 'center',
    render: (ls) => renderState(ls),
  },
  {
    id: 'source',
    header: 'Source',
    align: 'left',
    render: (_ls, src) => (
      <span className="font-mono text-stone-300 whitespace-nowrap">{src.node}</span>
    ),
  },
  {
    id: 'srcIface',
    header: 'Src Interface',
    align: 'left',
    render: (ls, src, _tgt, vl) => (
      <span className="font-mono text-stone-400 whitespace-nowrap text-[11px]">
        {formatIface(ls.source_node, src.iface, vl)}
      </span>
    ),
  },
  {
    id: 'srcVlan',
    header: 'Src VLAN',
    align: 'center',
    render: (ls) => renderVlanBadge(ls.source_vlan_tag),
  },
  {
    id: 'vni',
    header: 'VNI',
    align: 'center',
    render: (ls) => (
      ls.vni != null ? (
        <span className="font-mono text-[10px] px-1 py-0.5 bg-violet-950/40 text-violet-400 rounded">
          {ls.vni}
        </span>
      ) : (
        <span className="text-stone-600">-</span>
      )
    ),
  },
  {
    id: 'tgtVlan',
    header: 'Tgt VLAN',
    align: 'center',
    render: (ls) => renderVlanBadge(ls.target_vlan_tag),
  },
  {
    id: 'target',
    header: 'Target',
    align: 'left',
    render: (_ls, _src, tgt) => (
      <span className="font-mono text-stone-300 whitespace-nowrap">{tgt.node}</span>
    ),
  },
  {
    id: 'tgtIface',
    header: 'Tgt Interface',
    align: 'left',
    render: (ls, _src, tgt, vl) => (
      <span className="font-mono text-stone-400 whitespace-nowrap text-[11px]">
        {formatIface(ls.target_node, tgt.iface, vl)}
      </span>
    ),
  },
  {
    id: 'error',
    header: 'Error',
    align: 'left',
    render: (ls) => renderError(ls),
  },
];

const DEFAULT_LOCAL_ORDER = LOCAL_COLUMNS.map(c => c.id);
const DEFAULT_CROSS_HOST_ORDER = CROSS_HOST_COLUMNS.map(c => c.id);

// ─── Column Ordering Hook ─────────────────────────────────────────

/** Validates persisted column order against current column defs, reconciling additions/removals. */
function reconcileOrder(storedOrder: string[], columnDefs: ColumnDef[]): string[] {
  const validIds = new Set(columnDefs.map(c => c.id));
  // Keep stored columns that still exist
  const kept = storedOrder.filter(id => validIds.has(id));
  // Append any new columns not in stored order
  const keptSet = new Set(kept);
  const added = columnDefs.filter(c => !keptSet.has(c.id)).map(c => c.id);
  return [...kept, ...added];
}

function useColumnOrder(
  storageKey: string,
  columnDefs: ColumnDef[],
  defaultOrder: string[],
): [ColumnDef[], (fromIdx: number, toIdx: number) => void] {
  const [order, setOrder] = usePersistedState<string[]>(storageKey, defaultOrder);

  const orderedColumns = useMemo(() => {
    const reconciled = reconcileOrder(order, columnDefs);
    const colMap = new Map(columnDefs.map(c => [c.id, c]));
    return reconciled.map(id => colMap.get(id)!).filter(Boolean);
  }, [order, columnDefs]);

  const reorder = useCallback((fromIdx: number, toIdx: number) => {
    setOrder(prev => {
      const reconciled = reconcileOrder(prev, columnDefs);
      const next = [...reconciled];
      const [moved] = next.splice(fromIdx, 1);
      next.splice(toIdx, 0, moved);
      return next;
    });
  }, [setOrder, columnDefs]);

  return [orderedColumns, reorder];
}

// ─── Draggable Header ─────────────────────────────────────────────

function DraggableHeader({
  col,
  index,
  dragState,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: {
  col: ColumnDef;
  index: number;
  dragState: { dragging: number | null; over: number | null };
  onDragStart: (e: React.DragEvent, idx: number) => void;
  onDragOver: (e: React.DragEvent, idx: number) => void;
  onDrop: (idx: number) => void;
  onDragEnd: () => void;
}) {
  const isDragging = dragState.dragging === index;
  const isOver = dragState.over === index && dragState.dragging !== index;
  const dropSide = dragState.dragging !== null && dragState.over === index
    ? (dragState.dragging < index ? 'right' : 'left')
    : null;

  return (
    <th
      draggable
      onDragStart={(e) => onDragStart(e, index)}
      onDragOver={(e) => onDragOver(e, index)}
      onDrop={() => onDrop(index)}
      onDragEnd={onDragEnd}
      className={`px-2 py-1 font-semibold select-none cursor-grab active:cursor-grabbing transition-opacity ${
        col.align === 'center' ? 'text-center' : 'text-left'
      } ${isDragging ? 'opacity-40' : ''} ${
        isOver && dropSide === 'left' ? 'border-l-2 border-l-blue-500/60' : ''
      } ${isOver && dropSide === 'right' ? 'border-r-2 border-r-blue-500/60' : ''}`}
    >
      <span className="inline-flex items-center gap-1 group">
        <span className="text-stone-600 opacity-0 group-hover:opacity-100 transition-opacity text-[8px] leading-none">⠿</span>
        {col.header}
      </span>
    </th>
  );
}

// ─── Drag State Hook ──────────────────────────────────────────────

function useDragReorder(reorder: (from: number, to: number) => void) {
  const [dragState, setDragState] = useState<{ dragging: number | null; over: number | null }>({
    dragging: null,
    over: null,
  });
  const dragRef = useRef<number | null>(null);

  const onDragStart = useCallback((e: React.DragEvent, idx: number) => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', ''); // Required for Firefox
    dragRef.current = idx;
    setDragState({ dragging: idx, over: null });
  }, []);

  const onDragOver = useCallback((e: React.DragEvent, idx: number) => {
    e.preventDefault();
    setDragState(prev => (prev.over === idx ? prev : { ...prev, over: idx }));
  }, []);

  const onDrop = useCallback((toIdx: number) => {
    const fromIdx = dragRef.current;
    if (fromIdx !== null && fromIdx !== toIdx) {
      reorder(fromIdx, toIdx);
    }
    dragRef.current = null;
    setDragState({ dragging: null, over: null });
  }, [reorder]);

  const onDragEnd = useCallback(() => {
    dragRef.current = null;
    setDragState({ dragging: null, over: null });
  }, []);

  return { dragState, onDragStart, onDragOver, onDrop, onDragEnd };
}

// ─── Row Components ───────────────────────────────────────────────

function LinkRow({
  ls,
  columns,
  vendorLookup,
  isSelected,
  onSelect,
}: {
  ls: LinkStateData;
  columns: ColumnDef[];
  vendorLookup: Map<string, string>;
  isSelected: boolean;
  onSelect: (name: string) => void;
}) {
  const [src, tgt] = parseLinkEndpoints(ls.link_name);
  const rowTint = ROW_TINTS[ls.actual_state] || '';
  const selClass = isSelected ? 'ring-1 ring-stone-500' : '';

  return (
    <tr
      className={`border-b border-stone-800/50 hover:bg-stone-800/30 cursor-pointer transition-colors ${rowTint} ${selClass}`}
      onClick={() => onSelect(ls.link_name)}
    >
      {columns.map(col => (
        <td
          key={col.id}
          className={`px-2 py-1.5 ${col.align === 'center' ? 'text-center' : 'text-left'} ${
            col.id === 'error' ? 'max-w-[160px]' : ''
          }`}
        >
          {col.render(ls, src, tgt, vendorLookup)}
        </td>
      ))}
    </tr>
  );
}

// ─── Main Component ───────────────────────────────────────────────

const LinkTable: React.FC<LinkTableProps> = ({
  hostGroups,
  crossHostLinks,
  vendorLookup,
  selectedLinkName,
  onSelectLink,
}) => {
  const [hostFilter, setHostFilter] = useState<string | null>(null);

  const [localColumns, reorderLocal] = useColumnOrder(
    'infraLinkTable_localColumnOrder',
    LOCAL_COLUMNS,
    DEFAULT_LOCAL_ORDER,
  );
  const [crossHostCols, reorderCrossHost] = useColumnOrder(
    'infraLinkTable_crossHostColumnOrder',
    CROSS_HOST_COLUMNS,
    DEFAULT_CROSS_HOST_ORDER,
  );

  const localDrag = useDragReorder(reorderLocal);
  const crossHostDrag = useDragReorder(reorderCrossHost);

  // Build filtered local links per host
  const filteredLocalGroups = useMemo(() => {
    const groups = hostFilter
      ? hostGroups.filter(g => g.agentId === hostFilter)
      : hostGroups;
    return groups
      .filter(g => g.localLinks.length > 0)
      .map(g => ({ ...g, sortedLinks: sortLinks(g.localLinks) }));
  }, [hostGroups, hostFilter]);

  // Build filtered cross-host links
  const filteredCrossHost = useMemo(() => {
    let links = crossHostLinks;
    if (hostFilter) {
      links = links.filter(
        ls => ls.source_host_id === hostFilter || ls.target_host_id === hostFilter,
      );
    }
    return sortLinks(links);
  }, [crossHostLinks, hostFilter]);

  const totalLocal = filteredLocalGroups.reduce((s, g) => s + g.sortedLinks.length, 0);
  const hasAny = totalLocal > 0 || filteredCrossHost.length > 0;

  return (
    <>
      {/* Host filter bar */}
      <div className="flex items-center gap-1.5 px-4 py-2 border-b border-stone-800/50 flex-shrink-0">
        <span className="text-[10px] text-stone-500 uppercase font-bold tracking-wider mr-1">Filter</span>
        <button
          onClick={() => setHostFilter(null)}
          className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
            hostFilter === null
              ? 'border-stone-500 bg-stone-700/50 text-stone-200'
              : 'border-stone-700/50 text-stone-500 hover:text-stone-300 hover:border-stone-600'
          }`}
        >
          All
        </button>
        {hostGroups
          .filter(g => g.agentId && g.agentId !== '')
          .map(g => {
            const color = getAgentColor(g.agentId);
            const active = hostFilter === g.agentId;
            return (
              <button
                key={g.agentId}
                onClick={() => setHostFilter(active ? null : g.agentId)}
                className={`inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                  active
                    ? 'border-stone-500 bg-stone-700/50 text-stone-200'
                    : 'border-stone-700/50 text-stone-500 hover:text-stone-300 hover:border-stone-600'
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ backgroundColor: color }}
                />
                {g.hostName}
              </button>
            );
          })}
      </div>

      {/* Scrollable content */}
      <div className="flex-1 min-h-0 overflow-auto px-4 py-2">
        {!hasAny && (
          <div className="text-xs text-stone-600 italic py-2">No links to display</div>
        )}

        {/* Local Links section */}
        {filteredLocalGroups.length > 0 && (
          <div className="mb-3">
            <div className="text-[10px] text-stone-500 uppercase font-bold tracking-wider mb-1.5">
              Local Links ({totalLocal})
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-stone-500 uppercase border-b border-stone-700/50">
                  {localColumns.map((col, i) => (
                    <DraggableHeader
                      key={col.id}
                      col={col}
                      index={i}
                      dragState={localDrag.dragState}
                      onDragStart={localDrag.onDragStart}
                      onDragOver={localDrag.onDragOver}
                      onDrop={localDrag.onDrop}
                      onDragEnd={localDrag.onDragEnd}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredLocalGroups.map(group => (
                  <React.Fragment key={group.hostId}>
                    {/* Host sub-header when showing all */}
                    {hostFilter === null && hostGroups.filter(g => g.localLinks.length > 0).length > 1 && (
                      <tr>
                        <td colSpan={localColumns.length} className="px-2 pt-2 pb-0.5">
                          <div className="flex items-center gap-1.5">
                            <span
                              className="w-2 h-2 rounded-full flex-shrink-0"
                              style={{ backgroundColor: getAgentColor(group.agentId) }}
                            />
                            <span className="text-[10px] text-stone-400 font-medium">
                              {group.hostName}
                            </span>
                          </div>
                        </td>
                      </tr>
                    )}
                    {group.sortedLinks.map(ls => (
                      <LinkRow
                        key={ls.link_name}
                        ls={ls}
                        columns={localColumns}
                        vendorLookup={vendorLookup}
                        isSelected={selectedLinkName === ls.link_name}
                        onSelect={onSelectLink}
                      />
                    ))}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Cross-Host Links section */}
        {filteredCrossHost.length > 0 && (
          <div>
            <div className="text-[10px] text-stone-500 uppercase font-bold tracking-wider mb-1.5">
              Cross-Host Links ({filteredCrossHost.length})
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-stone-500 uppercase border-b border-stone-700/50">
                  {crossHostCols.map((col, i) => (
                    <DraggableHeader
                      key={col.id}
                      col={col}
                      index={i}
                      dragState={crossHostDrag.dragState}
                      onDragStart={crossHostDrag.onDragStart}
                      onDragOver={crossHostDrag.onDragOver}
                      onDrop={crossHostDrag.onDrop}
                      onDragEnd={crossHostDrag.onDragEnd}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredCrossHost.map(ls => (
                  <LinkRow
                    key={ls.link_name}
                    ls={ls}
                    columns={crossHostCols}
                    vendorLookup={vendorLookup}
                    isSelected={selectedLinkName === ls.link_name}
                    onSelect={onSelectLink}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
};

export default LinkTable;
