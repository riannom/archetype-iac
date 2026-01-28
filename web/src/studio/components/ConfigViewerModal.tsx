import React, { useEffect, useState } from 'react';
import DetailPopup from './DetailPopup';

interface SavedConfig {
  node_name: string;
  config: string;
  last_modified: number;
  exists: boolean;
}

interface ConfigViewerModalProps {
  isOpen: boolean;
  onClose: () => void;
  labId: string;
  /** If provided, show only this node's config. Otherwise show all configs with tabs. */
  nodeId?: string;
  nodeName?: string;
  studioRequest: <T>(path: string, options?: RequestInit) => Promise<T>;
}

const ConfigViewerModal: React.FC<ConfigViewerModalProps> = ({
  isOpen,
  onClose,
  labId,
  nodeId,
  nodeName,
  studioRequest,
}) => {
  const [configs, setConfigs] = useState<SavedConfig[]>([]);
  const [activeTab, setActiveTab] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!isOpen || !labId) return;

    const fetchConfigs = async () => {
      setLoading(true);
      setError(null);
      try {
        if (nodeName) {
          // Fetch single node config
          const data = await studioRequest<SavedConfig>(
            `/labs/${labId}/configs/${encodeURIComponent(nodeName)}`
          );
          setConfigs([data]);
          setActiveTab(data.node_name);
        } else {
          // Fetch all configs
          const data = await studioRequest<{ configs: SavedConfig[] }>(
            `/labs/${labId}/configs`
          );
          setConfigs(data.configs || []);
          if (data.configs && data.configs.length > 0) {
            setActiveTab(data.configs[0].node_name);
          }
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to load configs';
        setError(message);
        setConfigs([]);
      } finally {
        setLoading(false);
      }
    };

    fetchConfigs();
  }, [isOpen, labId, nodeName, studioRequest]);

  const handleCopy = async () => {
    const activeConfig = configs.find(c => c.node_name === activeTab);
    if (activeConfig) {
      await navigator.clipboard.writeText(activeConfig.config);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const formatTimestamp = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleString();
  };

  const activeConfig = configs.find(c => c.node_name === activeTab);

  const title = nodeName ? `Config: ${nodeName}` : 'Saved Configurations';

  return (
    <DetailPopup isOpen={isOpen} onClose={onClose} title={title} width="max-w-4xl">
      {loading && (
        <div className="flex items-center justify-center py-12">
          <i className="fa-solid fa-spinner fa-spin text-2xl text-stone-400" />
        </div>
      )}

      {error && (
        <div className="py-12 text-center">
          <i className="fa-solid fa-exclamation-circle text-2xl text-red-500 mb-2" />
          <p className="text-sm text-stone-500 dark:text-stone-400">{error}</p>
        </div>
      )}

      {!loading && !error && configs.length === 0 && (
        <div className="py-12 text-center">
          <i className="fa-solid fa-file-code text-3xl text-stone-300 dark:text-stone-700 mb-3" />
          <p className="text-sm text-stone-500 dark:text-stone-400">
            No saved configurations found.
          </p>
          <p className="text-xs text-stone-400 dark:text-stone-600 mt-2">
            Run "Extract Configs" from Runtime Control to save device configurations.
          </p>
        </div>
      )}

      {!loading && !error && configs.length > 0 && (
        <div className="flex flex-col h-[60vh]">
          {/* Tabs - only show if multiple configs */}
          {configs.length > 1 && (
            <div className="flex border-b border-stone-200 dark:border-stone-700 mb-4 overflow-x-auto">
              {configs.map((config) => (
                <button
                  key={config.node_name}
                  onClick={() => setActiveTab(config.node_name)}
                  className={`px-4 py-2 text-xs font-bold uppercase tracking-wide border-b-2 transition-all whitespace-nowrap ${
                    activeTab === config.node_name
                      ? 'text-sage-600 dark:text-sage-400 border-sage-500'
                      : 'text-stone-500 border-transparent hover:text-stone-700 dark:hover:text-stone-300'
                  }`}
                >
                  {config.node_name}
                </button>
              ))}
            </div>
          )}

          {/* Config header with metadata */}
          {activeConfig && (
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs text-stone-500 dark:text-stone-400">
                <i className="fa-solid fa-clock mr-1" />
                Last modified: {formatTimestamp(activeConfig.last_modified)}
              </div>
              <button
                onClick={handleCopy}
                className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-700 dark:text-stone-300 rounded-lg transition-colors"
              >
                <i className={`fa-solid ${copied ? 'fa-check' : 'fa-copy'}`} />
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
          )}

          {/* Config content */}
          {activeConfig && (
            <div className="flex-1 overflow-auto bg-stone-950 rounded-lg border border-stone-800">
              <pre className="p-4 text-xs font-mono text-sage-400 whitespace-pre overflow-x-auto">
                {activeConfig.config}
              </pre>
            </div>
          )}
        </div>
      )}
    </DetailPopup>
  );
};

export default ConfigViewerModal;
