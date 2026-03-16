import React, { useCallback, useMemo } from 'react';
import { TestSpec, TestSpecType, Node, Link, isDeviceNode } from '../types';
import { Select } from '../../components/ui/Select';

interface TestBuilderProps {
  specs: TestSpec[];
  onUpdateSpecs: (specs: TestSpec[]) => void;
  nodes: Node[];
  links: Link[];
  disabled?: boolean;
}

/** Generate a human-readable name from a test spec. */
function autoName(spec: TestSpec): string {
  switch (spec.type) {
    case 'ping':
      return `Ping ${spec.source || '?'} \u2192 ${spec.target || '?'}`;
    case 'command':
      return `Run "${spec.cmd || '?'}" on ${spec.node || '?'}`;
    case 'link_state':
      return `Link ${spec.link_name || '?'} ${spec.expected_state || 'up'}`;
    case 'node_state':
      return `Node ${spec.node_name || '?'} ${spec.expected_state || 'running'}`;
    default:
      return 'Test';
  }
}

const TEMPLATES: { type: TestSpecType; icon: string; label: string; defaults: Partial<TestSpec> }[] = [
  { type: 'ping', icon: 'fa-satellite-dish', label: 'Ping', defaults: { count: 3 } },
  { type: 'link_state', icon: 'fa-link', label: 'Link', defaults: { expected_state: 'up' } },
  { type: 'node_state', icon: 'fa-server', label: 'Node', defaults: { expected_state: 'running' } },
  { type: 'command', icon: 'fa-terminal', label: 'Command', defaults: {} },
];

const TestBuilder: React.FC<TestBuilderProps> = ({ specs, onUpdateSpecs, nodes, links, disabled }) => {
  const deviceNodes = useMemo(() => nodes.filter(isDeviceNode), [nodes]);

  const linkOptions = useMemo(() => {
    const nodeMap = new Map(nodes.map(n => [n.id, n.name]));
    return links.map(l => {
      const srcName = nodeMap.get(l.source) || l.source;
      const tgtName = nodeMap.get(l.target) || l.target;
      const srcIf = l.sourceInterface || 'eth1';
      const tgtIf = l.targetInterface || 'eth1';
      // Sort alphabetically as backend expects
      const [a, b] = [`${srcName}:${srcIf}`, `${tgtName}:${tgtIf}`].sort();
      return `${a}-${b}`;
    });
  }, [nodes, links]);

  const addSpec = useCallback((type: TestSpecType, defaults: Partial<TestSpec>) => {
    const spec: TestSpec = { type, ...defaults };
    // Pre-fill first node/link if available
    if ((type === 'ping' || type === 'command') && deviceNodes.length > 0) {
      if (type === 'ping') spec.source = deviceNodes[0].name;
      if (type === 'command') spec.node = deviceNodes[0].name;
    }
    if (type === 'node_state' && deviceNodes.length > 0) {
      spec.node_name = deviceNodes[0].name;
    }
    if (type === 'link_state' && linkOptions.length > 0) {
      spec.link_name = linkOptions[0];
    }
    onUpdateSpecs([...specs, spec]);
  }, [specs, onUpdateSpecs, deviceNodes, linkOptions]);

  const updateSpec = useCallback((index: number, patch: Partial<TestSpec>) => {
    const next = specs.map((s, i) => (i === index ? { ...s, ...patch } : s));
    onUpdateSpecs(next);
  }, [specs, onUpdateSpecs]);

  const removeSpec = useCallback((index: number) => {
    onUpdateSpecs(specs.filter((_, i) => i !== index));
  }, [specs, onUpdateSpecs]);

  const moveSpec = useCallback((index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= specs.length) return;
    const next = [...specs];
    [next[index], next[target]] = [next[target], next[index]];
    onUpdateSpecs(next);
  }, [specs, onUpdateSpecs]);

  return (
    <div className="flex flex-col h-full">
      {/* Template buttons */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-stone-200 dark:border-stone-700 flex-shrink-0">
        {TEMPLATES.map(t => (
          <button
            key={t.type}
            onClick={() => addSpec(t.type, t.defaults)}
            disabled={disabled}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-300 hover:bg-sage-100 dark:hover:bg-sage-900/40 hover:text-sage-700 dark:hover:text-sage-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <i className={`fa-solid ${t.icon} text-[11px]`} />
            <span>+{t.label}</span>
          </button>
        ))}
      </div>

      {/* Spec cards */}
      <div className="flex-1 overflow-y-auto">
        {specs.length === 0 && (
          <div className="flex items-center justify-center h-full text-stone-400 dark:text-stone-400 text-sm">
            <div className="text-center px-6">
              <i className="fa-solid fa-plus-circle text-2xl mb-2 block" />
              <p>Add tests using the buttons above</p>
              <p className="text-xs mt-1">or import from your topology YAML</p>
            </div>
          </div>
        )}
        {specs.map((spec, i) => (
          <SpecCard
            key={i}
            spec={spec}
            index={i}
            total={specs.length}
            deviceNodes={deviceNodes}
            linkOptions={linkOptions}
            disabled={disabled}
            onUpdate={(patch) => updateSpec(i, patch)}
            onRemove={() => removeSpec(i)}
            onMove={(dir) => moveSpec(i, dir)}
          />
        ))}
      </div>
    </div>
  );
};

interface SpecCardProps {
  spec: TestSpec;
  index: number;
  total: number;
  deviceNodes: Node[];
  linkOptions: string[];
  disabled?: boolean;
  onUpdate: (patch: Partial<TestSpec>) => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
}

const SpecCard: React.FC<SpecCardProps> = ({
  spec, index, total, deviceNodes, linkOptions, disabled, onUpdate, onRemove, onMove,
}) => {
  const title = spec.name || autoName(spec);
  const typeInfo = TEMPLATES.find(t => t.type === spec.type) || TEMPLATES[0];

  return (
    <div className="mx-3 my-2 rounded-lg border border-stone-200 dark:border-stone-700 bg-stone-50 dark:bg-stone-800/50 overflow-hidden" data-testid={`spec-card-${index}`}>
      {/* Card header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-stone-100 dark:bg-stone-800">
        <div className="flex items-center gap-1.5 min-w-0">
          <i className={`fa-solid ${typeInfo.icon} text-[11px] text-sage-500`} />
          <span className="text-xs font-medium text-stone-600 dark:text-stone-300 truncate">{title}</span>
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button
            onClick={() => onMove(-1)}
            disabled={disabled || index === 0}
            className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-200 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Move up"
          >
            <i className="fa-solid fa-chevron-up text-[11px]" />
          </button>
          <button
            onClick={() => onMove(1)}
            disabled={disabled || index === total - 1}
            className="p-1 text-stone-400 hover:text-stone-600 dark:hover:text-stone-200 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Move down"
          >
            <i className="fa-solid fa-chevron-down text-[11px]" />
          </button>
          <button
            onClick={onRemove}
            disabled={disabled}
            className="p-1 text-stone-400 hover:text-red-500 disabled:opacity-30 disabled:cursor-not-allowed"
            title="Remove test"
          >
            <i className="fa-solid fa-times text-[11px]" />
          </button>
        </div>
      </div>

      {/* Card body - type-specific fields */}
      <div className="px-3 py-2 space-y-1.5">
        {spec.type === 'ping' && (
          <PingFields spec={spec} deviceNodes={deviceNodes} disabled={disabled} onUpdate={onUpdate} />
        )}
        {spec.type === 'command' && (
          <CommandFields spec={spec} deviceNodes={deviceNodes} disabled={disabled} onUpdate={onUpdate} />
        )}
        {spec.type === 'link_state' && (
          <LinkStateFields spec={spec} linkOptions={linkOptions} disabled={disabled} onUpdate={onUpdate} />
        )}
        {spec.type === 'node_state' && (
          <NodeStateFields spec={spec} deviceNodes={deviceNodes} disabled={disabled} onUpdate={onUpdate} />
        )}
      </div>
    </div>
  );
};

// ── Field row helper ──

const FieldRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-center gap-2">
    <label className="text-xs text-stone-500 dark:text-stone-400 w-14 flex-shrink-0">{label}</label>
    {children}
  </div>
);

const selectClass = "flex-1";
const inputClass = selectClass;

// ── Per-type field components ──

interface FieldProps {
  spec: TestSpec;
  deviceNodes: Node[];
  linkOptions?: string[];
  disabled?: boolean;
  onUpdate: (patch: Partial<TestSpec>) => void;
}

const PingFields: React.FC<FieldProps> = ({ spec, deviceNodes, disabled, onUpdate }) => (
  <>
    <FieldRow label="Source">
      <Select value={spec.source || ''} onChange={e => onUpdate({ source: e.target.value })} disabled={disabled} size="sm" className={selectClass}>
        <option value="">Select node...</option>
        {deviceNodes.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
      </Select>
    </FieldRow>
    <FieldRow label="Target">
      <input
        type="text"
        value={spec.target || ''}
        onChange={e => onUpdate({ target: e.target.value })}
        placeholder="IP address or hostname"
        disabled={disabled}
        className={inputClass}
      />
    </FieldRow>
    <FieldRow label="Count">
      <input
        type="number"
        value={spec.count ?? 3}
        onChange={e => onUpdate({ count: parseInt(e.target.value) || 1 })}
        min={1}
        max={100}
        disabled={disabled}
        className={inputClass}
        style={{ maxWidth: 80 }}
      />
    </FieldRow>
  </>
);

const CommandFields: React.FC<FieldProps> = ({ spec, deviceNodes, disabled, onUpdate }) => (
  <>
    <FieldRow label="Node">
      <Select value={spec.node || ''} onChange={e => onUpdate({ node: e.target.value })} disabled={disabled} size="sm" className={selectClass}>
        <option value="">Select node...</option>
        {deviceNodes.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
      </Select>
    </FieldRow>
    <FieldRow label="Command">
      <input
        type="text"
        value={spec.cmd || ''}
        onChange={e => onUpdate({ cmd: e.target.value })}
        placeholder="show version"
        disabled={disabled}
        className={inputClass}
      />
    </FieldRow>
    <FieldRow label="Expect">
      <input
        type="text"
        value={spec.expect || ''}
        onChange={e => onUpdate({ expect: e.target.value })}
        placeholder="Regex pattern (optional)"
        disabled={disabled}
        className={inputClass}
      />
    </FieldRow>
  </>
);

const LinkStateFields: React.FC<Omit<FieldProps, 'deviceNodes'> & { linkOptions: string[] }> = ({ spec, linkOptions, disabled, onUpdate }) => (
  <>
    <FieldRow label="Link">
      <Select value={spec.link_name || ''} onChange={e => onUpdate({ link_name: e.target.value })} disabled={disabled} size="sm" className={selectClass}>
        <option value="">Select link...</option>
        {linkOptions.map(l => <option key={l} value={l}>{l}</option>)}
      </Select>
    </FieldRow>
    <FieldRow label="State">
      <Select value={spec.expected_state || 'up'} onChange={e => onUpdate({ expected_state: e.target.value })} disabled={disabled} size="sm" className={selectClass}
        options={[{ value: 'up', label: 'up' }, { value: 'down', label: 'down' }]}
      />
    </FieldRow>
  </>
);

const NodeStateFields: React.FC<FieldProps> = ({ spec, deviceNodes, disabled, onUpdate }) => (
  <>
    <FieldRow label="Node">
      <Select value={spec.node_name || ''} onChange={e => onUpdate({ node_name: e.target.value })} disabled={disabled} size="sm" className={selectClass}>
        <option value="">Select node...</option>
        {deviceNodes.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
      </Select>
    </FieldRow>
    <FieldRow label="State">
      <Select value={spec.expected_state || 'running'} onChange={e => onUpdate({ expected_state: e.target.value })} disabled={disabled} size="sm" className={selectClass}
        options={[{ value: 'running', label: 'running' }, { value: 'stopped', label: 'stopped' }]}
      />
    </FieldRow>
  </>
);

export default TestBuilder;
