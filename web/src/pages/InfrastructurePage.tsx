import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useTheme, ThemeSelector } from '../theme/index';
import { useUser } from '../contexts/UserContext';
import { apiRequest } from '../api';
import { ArchetypeIcon } from '../components/icons';
import { formatTimestamp } from '../utils/format';

interface InfraSettings {
  overlay_mtu: number;
  mtu_verification_enabled: boolean;
  updated_at: string | null;
  updated_by_id: string | null;
}

interface AgentMeshNode {
  id: string;
  name: string;
  address: string;
  status: string;
}

interface AgentLinkOut {
  id: string;
  source_agent_id: string;
  source_agent_name: string | null;
  target_agent_id: string;
  target_agent_name: string | null;
  link_type: string;
  configured_mtu: number;
  tested_mtu: number | null;
  last_test_at: string | null;
  test_status: string;
  test_error: string | null;
  latency_ms: number | null;
}

interface AgentMeshResponse {
  agents: AgentMeshNode[];
  links: AgentLinkOut[];
  settings: InfraSettings;
}

interface MtuTestResponse {
  success: boolean;
  source_agent_id: string;
  target_agent_id: string;
  configured_mtu: number;
  tested_mtu: number | null;
  link_type: string | null;
  latency_ms: number | null;
  error: string | null;
}

interface MtuTestAllResponse {
  total_pairs: number;
  successful: number;
  failed: number;
  results: MtuTestResponse[];
}

const InfrastructurePage: React.FC = () => {
  const { effectiveMode, toggleMode } = useTheme();
  const { user, loading: userLoading } = useUser();
  const navigate = useNavigate();

  const [mesh, setMesh] = useState<AgentMeshResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showThemeSelector, setShowThemeSelector] = useState(false);

  // Settings form state
  const [mtuValue, setMtuValue] = useState<number>(1450);
  const [verificationEnabled, setVerificationEnabled] = useState<boolean>(true);
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsDirty, setSettingsDirty] = useState(false);

  // Testing state
  const [testingAll, setTestingAll] = useState(false);
  const [testingLink, setTestingLink] = useState<string | null>(null);

  const loadMesh = useCallback(async () => {
    try {
      const data = await apiRequest<AgentMeshResponse>('/infrastructure/mesh');
      setMesh(data);
      setMtuValue(data.settings.overlay_mtu);
      setVerificationEnabled(data.settings.mtu_verification_enabled);
      setSettingsDirty(false);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load infrastructure data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMesh();
    const interval = setInterval(loadMesh, 30000);
    return () => clearInterval(interval);
  }, [loadMesh]);

  // Redirect non-admins
  if (!userLoading && user && !user.is_admin) {
    return <Navigate to="/" replace />;
  }

  if (!userLoading && !user) {
    return <Navigate to="/" replace />;
  }

  const handleMtuChange = (value: number) => {
    setMtuValue(value);
    setSettingsDirty(value !== mesh?.settings.overlay_mtu || verificationEnabled !== mesh?.settings.mtu_verification_enabled);
  };

  const handleVerificationChange = (value: boolean) => {
    setVerificationEnabled(value);
    setSettingsDirty(mtuValue !== mesh?.settings.overlay_mtu || value !== mesh?.settings.mtu_verification_enabled);
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      await apiRequest('/infrastructure/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          overlay_mtu: mtuValue,
          mtu_verification_enabled: verificationEnabled,
        }),
      });
      setSettingsDirty(false);
      await loadMesh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setSavingSettings(false);
    }
  };

  const testAllLinks = async () => {
    setTestingAll(true);
    try {
      const result = await apiRequest<MtuTestAllResponse>('/infrastructure/mesh/test-all', {
        method: 'POST',
      });
      await loadMesh();
      if (result.failed > 0) {
        alert(`MTU tests completed: ${result.successful} passed, ${result.failed} failed`);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to run MTU tests');
    } finally {
      setTestingAll(false);
    }
  };

  const testLink = async (sourceId: string, targetId: string) => {
    const linkKey = `${sourceId}-${targetId}`;
    setTestingLink(linkKey);
    try {
      await apiRequest<MtuTestResponse>('/infrastructure/mesh/test-mtu', {
        method: 'POST',
        body: JSON.stringify({
          source_agent_id: sourceId,
          target_agent_id: targetId,
        }),
      });
      await loadMesh();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to test link');
    } finally {
      setTestingLink(null);
    }
  };

  const getStatusColor = (status: string): string => {
    switch (status) {
      case 'success':
        return 'bg-green-500';
      case 'failed':
        return 'bg-red-500';
      case 'pending':
        return 'bg-amber-500';
      default:
        return 'bg-stone-400';
    }
  };

  const getStatusBadgeStyle = (status: string): string => {
    switch (status) {
      case 'success':
        return 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 border-green-300 dark:border-green-700';
      case 'failed':
        return 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 border-red-300 dark:border-red-700';
      case 'pending':
        return 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-700';
      default:
        return 'bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 border-stone-300 dark:border-stone-700';
    }
  };

  const getLinkTypeBadge = (linkType: string): string => {
    switch (linkType) {
      case 'direct':
        return 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400';
      case 'routed':
        return 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400';
      default:
        return 'bg-stone-100 dark:bg-stone-800 text-stone-500 dark:text-stone-400';
    }
  };

  // Group links by source->target pair for display
  const linkPairs = mesh?.links.reduce((acc, link) => {
    const key = [link.source_agent_id, link.target_agent_id].sort().join('--');
    if (!acc[key]) {
      acc[key] = [];
    }
    acc[key].push(link);
    return acc;
  }, {} as Record<string, AgentLinkOut[]>) ?? {};

  return (
    <>
      <div className="min-h-screen bg-stone-50 dark:bg-stone-900 flex flex-col overflow-hidden">
        <header className="h-20 border-b border-stone-200 dark:border-stone-800 bg-white/30 dark:bg-stone-900/30 flex items-center justify-between px-10">
          <div className="flex items-center gap-4">
            <ArchetypeIcon size={40} className="text-sage-600 dark:text-sage-400" />
            <div>
              <h1 className="text-xl font-black text-stone-900 dark:text-white tracking-tight">ARCHETYPE</h1>
              <p className="text-[10px] text-sage-600 dark:text-sage-500 font-bold uppercase tracking-widest">Infrastructure Settings</p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/hosts')}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-arrow-left text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Hosts</span>
            </button>

            <button
              onClick={() => setShowThemeSelector(true)}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title="Theme Settings"
            >
              <i className="fa-solid fa-palette text-sm"></i>
            </button>

            <button
              onClick={toggleMode}
              className="w-9 h-9 flex items-center justify-center bg-stone-100 dark:bg-stone-800 text-stone-600 dark:text-stone-400 hover:text-sage-600 dark:hover:text-sage-400 rounded-lg transition-all border border-stone-300 dark:border-stone-700"
              title={`Switch to ${effectiveMode === 'dark' ? 'light' : 'dark'} mode`}
            >
              <i className={`fa-solid ${effectiveMode === 'dark' ? 'fa-sun' : 'fa-moon'} text-sm`}></i>
            </button>

            <button
              onClick={loadMesh}
              className="flex items-center gap-2 px-3 py-2 bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-300 border border-stone-300 dark:border-stone-700 rounded-lg transition-all"
            >
              <i className="fa-solid fa-rotate text-xs"></i>
              <span className="text-[10px] font-bold uppercase">Refresh</span>
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-10 custom-scrollbar">
          <div className="max-w-6xl mx-auto">
            {loading && !mesh ? (
              <div className="flex items-center justify-center py-20">
                <i className="fa-solid fa-spinner fa-spin text-stone-400 text-2xl"></i>
                <span className="ml-3 text-stone-500">Loading infrastructure data...</span>
              </div>
            ) : error ? (
              <div className="text-center py-20 text-red-500">
                <i className="fa-solid fa-exclamation-circle text-3xl mb-3"></i>
                <p>{error}</p>
              </div>
            ) : (
              <div className="space-y-8">
                {/* Settings Panel */}
                <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-2xl p-6">
                  <h2 className="text-lg font-bold text-stone-900 dark:text-white mb-4 flex items-center gap-2">
                    <i className="fa-solid fa-sliders text-sage-600 dark:text-sage-400"></i>
                    Global Settings
                  </h2>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Overlay MTU */}
                    <div>
                      <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                        Overlay MTU
                        <span className="text-stone-400 font-normal ml-2">(VXLAN tunnel MTU)</span>
                      </label>
                      <div className="flex items-center gap-3">
                        <input
                          type="number"
                          min={68}
                          max={9000}
                          value={mtuValue}
                          onChange={(e) => handleMtuChange(parseInt(e.target.value) || 1450)}
                          className="w-32 px-3 py-2 bg-stone-100 dark:bg-stone-800 border border-stone-300 dark:border-stone-700 rounded-lg text-stone-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-sage-500"
                        />
                        <span className="text-xs text-stone-500">bytes (68-9000)</span>
                      </div>
                      <p className="text-xs text-stone-400 mt-1">
                        Default is 1450 to account for ~50 byte VXLAN overhead on 1500 MTU networks.
                        Use 8950 for jumbo frame networks.
                      </p>
                    </div>

                    {/* MTU Verification */}
                    <div>
                      <label className="block text-sm font-medium text-stone-700 dark:text-stone-300 mb-2">
                        MTU Verification
                      </label>
                      <label className="flex items-center gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={verificationEnabled}
                          onChange={(e) => handleVerificationChange(e.target.checked)}
                          className="w-5 h-5 rounded border-stone-300 dark:border-stone-600 text-sage-600 focus:ring-sage-500"
                        />
                        <span className="text-sm text-stone-600 dark:text-stone-400">
                          Enable automatic MTU verification between agents
                        </span>
                      </label>
                    </div>
                  </div>

                  {/* Save Button */}
                  <div className="mt-6 flex items-center gap-4">
                    <button
                      onClick={saveSettings}
                      disabled={!settingsDirty || savingSettings}
                      className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-all text-sm font-medium ${
                        settingsDirty && !savingSettings
                          ? 'bg-sage-600 hover:bg-sage-700 text-white'
                          : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                      }`}
                    >
                      {savingSettings ? (
                        <i className="fa-solid fa-spinner fa-spin"></i>
                      ) : (
                        <i className="fa-solid fa-save"></i>
                      )}
                      Save Settings
                    </button>
                    {settingsDirty && (
                      <span className="text-xs text-amber-600 dark:text-amber-400">
                        <i className="fa-solid fa-circle-exclamation mr-1"></i>
                        Unsaved changes
                      </span>
                    )}
                    {mesh?.settings.updated_at && (
                      <span className="text-xs text-stone-400">
                        Last updated: {formatTimestamp(mesh.settings.updated_at)}
                      </span>
                    )}
                  </div>
                </div>

                {/* Agent Mesh */}
                <div className="bg-white dark:bg-stone-900 border border-stone-200 dark:border-stone-800 rounded-2xl p-6">
                  <div className="flex items-center justify-between mb-6">
                    <h2 className="text-lg font-bold text-stone-900 dark:text-white flex items-center gap-2">
                      <i className="fa-solid fa-diagram-project text-sage-600 dark:text-sage-400"></i>
                      Agent Mesh
                    </h2>
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-4 text-xs text-stone-500">
                        <div className="flex items-center gap-1.5">
                          <div className="w-3 h-3 rounded-full bg-green-500"></div>
                          <span>{mesh?.agents.filter(a => a.status === 'online').length} Online</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <div className="w-3 h-3 rounded-full bg-red-500"></div>
                          <span>{mesh?.agents.filter(a => a.status !== 'online').length} Offline</span>
                        </div>
                      </div>
                      <button
                        onClick={testAllLinks}
                        disabled={testingAll || (mesh?.agents.filter(a => a.status === 'online').length ?? 0) < 2}
                        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all text-xs font-medium ${
                          !testingAll && (mesh?.agents.filter(a => a.status === 'online').length ?? 0) >= 2
                            ? 'bg-blue-100 dark:bg-blue-900/30 hover:bg-blue-200 dark:hover:bg-blue-900/50 text-blue-700 dark:text-blue-400'
                            : 'bg-stone-200 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                        }`}
                      >
                        {testingAll ? (
                          <>
                            <i className="fa-solid fa-spinner fa-spin"></i>
                            Testing...
                          </>
                        ) : (
                          <>
                            <i className="fa-solid fa-network-wired"></i>
                            Test All
                          </>
                        )}
                      </button>
                    </div>
                  </div>

                  {/* Agent Grid */}
                  {mesh?.agents.length === 0 ? (
                    <div className="text-center py-12 text-stone-500">
                      <i className="fa-solid fa-server text-4xl mb-3 opacity-30"></i>
                      <p>No agents registered</p>
                    </div>
                  ) : mesh?.agents.length === 1 ? (
                    <div className="text-center py-12 text-stone-500">
                      <i className="fa-solid fa-link-slash text-4xl mb-3 opacity-30"></i>
                      <p>Only one agent registered. Add more agents to see mesh connectivity.</p>
                    </div>
                  ) : (
                    <>
                      {/* Agent Nodes */}
                      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 mb-6">
                        {mesh?.agents.map((agent) => (
                          <div
                            key={agent.id}
                            className="p-4 bg-stone-50 dark:bg-stone-800/50 border border-stone-200 dark:border-stone-700 rounded-xl"
                          >
                            <div className="flex items-center gap-2 mb-2">
                              <div className={`w-3 h-3 rounded-full ${agent.status === 'online' ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
                              <span className="font-medium text-stone-900 dark:text-white truncate">{agent.name}</span>
                            </div>
                            <p className="text-xs text-stone-500 truncate">{agent.address}</p>
                          </div>
                        ))}
                      </div>

                      {/* Link Details Table */}
                      <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-300 mb-3">Link Details</h3>
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-stone-200 dark:border-stone-700">
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Path</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Type</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">MTU</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Latency</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Status</th>
                              <th className="text-left py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Last Test</th>
                              <th className="text-right py-2 px-3 font-medium text-stone-500 dark:text-stone-400">Action</th>
                            </tr>
                          </thead>
                          <tbody>
                            {mesh?.links.map((link) => {
                              const linkKey = `${link.source_agent_id}-${link.target_agent_id}`;
                              const isTesting = testingLink === linkKey;

                              return (
                                <tr key={link.id} className="border-b border-stone-100 dark:border-stone-800 hover:bg-stone-50 dark:hover:bg-stone-800/30">
                                  <td className="py-2 px-3">
                                    <div className="flex items-center gap-2">
                                      <span className="font-medium text-stone-700 dark:text-stone-300">{link.source_agent_name}</span>
                                      <i className="fa-solid fa-arrow-right text-stone-400 text-xs"></i>
                                      <span className="font-medium text-stone-700 dark:text-stone-300">{link.target_agent_name}</span>
                                    </div>
                                  </td>
                                  <td className="py-2 px-3">
                                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${getLinkTypeBadge(link.link_type)}`}>
                                      {link.link_type}
                                    </span>
                                  </td>
                                  <td className="py-2 px-3 text-stone-600 dark:text-stone-400">
                                    {link.tested_mtu ? (
                                      <span className={link.tested_mtu >= link.configured_mtu ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}>
                                        {link.tested_mtu}
                                      </span>
                                    ) : (
                                      <span className="text-stone-400">-</span>
                                    )}
                                    <span className="text-stone-400 text-xs ml-1">/ {link.configured_mtu}</span>
                                  </td>
                                  <td className="py-2 px-3 text-stone-600 dark:text-stone-400">
                                    {link.latency_ms !== null ? (
                                      <span>{link.latency_ms.toFixed(2)} ms</span>
                                    ) : (
                                      <span className="text-stone-400">-</span>
                                    )}
                                  </td>
                                  <td className="py-2 px-3">
                                    <span className={`px-2 py-0.5 rounded text-xs font-medium border ${getStatusBadgeStyle(link.test_status)}`}>
                                      {link.test_status}
                                    </span>
                                    {link.test_error && (
                                      <span className="ml-2 text-xs text-red-500" title={link.test_error}>
                                        <i className="fa-solid fa-circle-exclamation"></i>
                                      </span>
                                    )}
                                  </td>
                                  <td className="py-2 px-3 text-xs text-stone-400">
                                    {link.last_test_at ? formatTimestamp(link.last_test_at) : '-'}
                                  </td>
                                  <td className="py-2 px-3 text-right">
                                    <button
                                      onClick={() => testLink(link.source_agent_id, link.target_agent_id)}
                                      disabled={isTesting || testingAll}
                                      className={`px-2 py-1 rounded text-xs font-medium transition-all ${
                                        !isTesting && !testingAll
                                          ? 'bg-stone-100 dark:bg-stone-800 hover:bg-stone-200 dark:hover:bg-stone-700 text-stone-600 dark:text-stone-400'
                                          : 'bg-stone-100 dark:bg-stone-800 text-stone-400 cursor-not-allowed'
                                      }`}
                                    >
                                      {isTesting ? (
                                        <i className="fa-solid fa-spinner fa-spin"></i>
                                      ) : (
                                        <i className="fa-solid fa-play"></i>
                                      )}
                                    </button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>

                      {mesh?.links.length === 0 && (
                        <div className="text-center py-8 text-stone-500">
                          <p className="text-sm">No links to display. Links are created automatically when agents are registered.</p>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        </main>

        <footer className="h-10 border-t border-stone-200 dark:border-stone-900 bg-stone-100 dark:bg-stone-950 flex items-center px-10 justify-between text-[10px] text-stone-500 dark:text-stone-600 font-medium">
          <span>Archetype Infrastructure Management</span>
          <span>Auto-refresh: 30s</span>
        </footer>
      </div>

      <ThemeSelector
        isOpen={showThemeSelector}
        onClose={() => setShowThemeSelector(false)}
      />
    </>
  );
};

export default InfrastructurePage;
