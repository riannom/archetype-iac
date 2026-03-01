import { useCallback, useEffect, useState } from 'react';
import { useNotifications } from '../../contexts/NotificationContext';
import { apiRequest } from '../../api';
import type {
  AgentMeshResponse,
  AgentNetworkConfig,
  HostDetailed,
  InterfaceDetail,
  InterfaceDetailsResponse,
  ManagedInterface,
  MtuTestAllResponse,
  MtuTestResponse,
} from './infrastructureTypes';

export function useMtuConfig(
  hosts: HostDetailed[],
  networkConfigs: AgentNetworkConfig[],
  managedInterfaces: ManagedInterface[],
  mesh: AgentMeshResponse | null,
  mtuValue: number,
  loadMesh: () => Promise<void>,
  loadNetworkConfigs: () => Promise<void>,
) {
  const { addNotification } = useNotifications();

  const notifyError = useCallback((title: string, err: unknown) => {
    addNotification('error', title, err instanceof Error ? err.message : undefined);
  }, [addNotification]);

  // Testing state
  const [testingAll, setTestingAll] = useState(false);
  const [testingLink, setTestingLink] = useState<string | null>(null);

  // MTU config modal state
  const [configuringMtu, setConfiguringMtu] = useState<string | null>(null);  // agent ID being configured
  const [configModalData, setConfigModalData] = useState<{
    agentId: string;
    agentName: string;
    interfaces: InterfaceDetail[];
    defaultInterface: string | null;
    networkManager: string | null;
    currentConfig: AgentNetworkConfig | null;
  } | null>(null);
  const [selectedInterface, setSelectedInterface] = useState<string>('');
  const [desiredMtu, setDesiredMtu] = useState<number>(9000);
  const [savingMtuConfig, setSavingMtuConfig] = useState(false);
  const [selectedTransportMode, setSelectedTransportMode] = useState<string>('management');
  const [selectedTransportInterface, setSelectedTransportInterface] = useState<string>('');
  const [useTransportInterface, setUseTransportInterface] = useState<boolean>(true);

  const testAllLinks = async () => {
    setTestingAll(true);
    try {
      const result = await apiRequest<MtuTestAllResponse>('/infrastructure/mesh/test-all', {
        method: 'POST',
      });
      await loadMesh();
      if (result.failed > 0) {
        addNotification(
          'warning',
          'MTU tests completed with failures',
          `${result.successful} passed, ${result.failed} failed`
        );
      }
    } catch (err) {
      notifyError('Failed to run MTU tests', err);
    } finally {
      setTestingAll(false);
    }
  };

  const testLink = async (sourceId: string, targetId: string, testPath: string) => {
    const linkKey = `${sourceId}-${targetId}-${testPath}`;
    setTestingLink(linkKey);
    try {
      await apiRequest<MtuTestResponse>('/infrastructure/mesh/test-mtu', {
        method: 'POST',
        body: JSON.stringify({
          source_agent_id: sourceId,
          target_agent_id: targetId,
          test_path: testPath,
        }),
      });
      await loadMesh();
    } catch (err) {
      notifyError('Failed to test link', err);
    } finally {
      setTestingLink(null);
    }
  };

  const openMtuConfigModal = async (agentId: string) => {
    const host = hosts.find(h => h.id === agentId);
    if (!host || host.status !== 'online') {
      addNotification('warning', 'Agent is offline');
      return;
    }

    setConfiguringMtu(agentId);
    try {
      const interfacesData = await apiRequest<InterfaceDetailsResponse>(
        `/infrastructure/agents/${agentId}/interfaces`
      );

      const existingConfig = networkConfigs.find(c => c.host_id === agentId);

      setConfigModalData({
        agentId,
        agentName: host.name,
        interfaces: interfacesData.interfaces.filter(i => i.is_physical),
        defaultInterface: interfacesData.default_route_interface,
        networkManager: interfacesData.network_manager,
        currentConfig: existingConfig || null,
      });

      const transportIfaces = managedInterfaces.filter(i => i.host_id === agentId && i.interface_type === 'transport');
      const subifaces = transportIfaces.filter(i => i.parent_interface && i.vlan_id !== null);
      const dedicatedIfaces = transportIfaces.filter(i => !i.vlan_id);

      // Pre-fill form with existing config or defaults
      if (existingConfig?.data_plane_interface) {
        setSelectedInterface(existingConfig.data_plane_interface);
        setDesiredMtu(existingConfig.desired_mtu);
      } else if (interfacesData.default_route_interface) {
        setSelectedInterface(interfacesData.default_route_interface);
        setDesiredMtu(9000);
      } else if (interfacesData.interfaces.length > 0) {
        const firstPhysical = interfacesData.interfaces.find(i => i.is_physical);
        setSelectedInterface(firstPhysical?.name || '');
        setDesiredMtu(9000);
      }

      setSelectedTransportMode(existingConfig?.transport_mode || 'management');
      setUseTransportInterface(
        (existingConfig?.transport_mode && existingConfig.transport_mode !== 'management') ? true : false
      );
      if (existingConfig?.transport_mode === 'subinterface') {
        const matched = subifaces.find(i =>
          (existingConfig.parent_interface && existingConfig.vlan_id !== null
            && i.parent_interface === existingConfig.parent_interface
            && i.vlan_id === existingConfig.vlan_id)
          || (existingConfig.transport_ip && i.ip_address
            && existingConfig.transport_ip.split('/')[0] === i.ip_address.split('/')[0])
        );
        setSelectedTransportInterface(matched?.name || subifaces[0]?.name || '');
      } else if (existingConfig?.transport_mode === 'dedicated') {
        const matched = dedicatedIfaces.find(i => i.name === existingConfig.data_plane_interface);
        setSelectedTransportInterface(matched?.name || dedicatedIfaces[0]?.name || '');
      } else {
        setSelectedTransportInterface('');
      }
    } catch (err) {
      notifyError('Failed to load interface details', err);
    } finally {
      setConfiguringMtu(null);
    }
  };

  const closeMtuConfigModal = () => {
    setConfigModalData(null);
    setSelectedInterface('');
    setDesiredMtu(9000);
    setSelectedTransportMode('management');
    setSelectedTransportInterface('');
    setUseTransportInterface(true);
  };

  const saveMtuConfig = async () => {
    if (!configModalData || !selectedInterface) return;

    setSavingMtuConfig(true);
    try {
      const transportIfaces = managedInterfaces.filter(
        i => i.host_id === configModalData.agentId && i.interface_type === 'transport'
      );
      const subifaces = transportIfaces.filter(i => i.parent_interface && i.vlan_id !== null);
      const dedicatedIfaces = transportIfaces.filter(i => !i.vlan_id);
      const chosenSub = subifaces.find(i => i.name === selectedTransportInterface);
      const chosenDedicated = dedicatedIfaces.find(i => i.name === selectedTransportInterface);

      const payload: Record<string, unknown> = {
        data_plane_interface: selectedInterface,
        desired_mtu: desiredMtu,
        transport_mode: selectedTransportMode,
      };

      if (selectedTransportMode === 'subinterface' && chosenSub) {
        payload.parent_interface = chosenSub.parent_interface;
        payload.vlan_id = chosenSub.vlan_id;
        payload.transport_ip = chosenSub.ip_address;
      } else if (selectedTransportMode === 'dedicated' && chosenDedicated) {
        payload.data_plane_interface = chosenDedicated.name;
        payload.transport_ip = chosenDedicated.ip_address;
      } else if (selectedTransportMode === 'management') {
        payload.parent_interface = null;
        payload.vlan_id = null;
        payload.transport_ip = null;
      }

      await apiRequest(`/infrastructure/agents/${configModalData.agentId}/network-config`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      });
      await loadNetworkConfigs();
      closeMtuConfigModal();
    } catch (err) {
      notifyError('Failed to save MTU configuration', err);
    } finally {
      setSavingMtuConfig(false);
    }
  };

  // Sync transport interface selection when modal data or transport mode changes
  useEffect(() => {
    if (!configModalData) return;
    const transportIfaces = managedInterfaces.filter(
      i => i.host_id === configModalData.agentId && i.interface_type === 'transport'
    );
    const subifaces = transportIfaces.filter(i => i.parent_interface && i.vlan_id !== null);
    const dedicatedIfaces = transportIfaces.filter(i => !i.vlan_id);

    if (selectedTransportMode === 'management') {
      setUseTransportInterface(false);
    }

    if (selectedTransportMode === 'subinterface' && !selectedTransportInterface && subifaces.length > 0) {
      setSelectedTransportInterface(subifaces[0].name);
      if (useTransportInterface) {
        setSelectedInterface(subifaces[0].name);
      }
    }
    if (selectedTransportMode === 'dedicated' && !selectedTransportInterface && dedicatedIfaces.length > 0) {
      setSelectedTransportInterface(dedicatedIfaces[0].name);
      if (useTransportInterface) {
        setSelectedInterface(dedicatedIfaces[0].name);
      }
    }
  }, [configModalData, managedInterfaces, selectedTransportMode, selectedTransportInterface, useTransportInterface]);

  // Compute MTU recommendation from data plane test results
  const mtuRecommendation = (() => {
    if (!mesh?.links.length) return null;
    const dpLinks = mesh.links.filter(l => l.test_path === 'data_plane' && l.test_status === 'success' && l.tested_mtu);
    if (dpLinks.length === 0) return null;
    const failedDpLinks = mesh.links.filter(l => l.test_path === 'data_plane' && l.test_status === 'failed');
    if (failedDpLinks.length > 0) return null;
    const minTestedMtu = Math.min(...dpLinks.map(l => l.tested_mtu!));
    const recommended = minTestedMtu - 50; // Account for VXLAN overhead
    return recommended > mtuValue ? recommended : null;
  })();

  return {
    // Testing
    testingAll,
    testingLink,
    testAllLinks,
    testLink,
    // MTU config modal
    configuringMtu,
    configModalData,
    selectedInterface,
    setSelectedInterface,
    desiredMtu,
    setDesiredMtu,
    savingMtuConfig,
    selectedTransportMode,
    setSelectedTransportMode,
    selectedTransportInterface,
    setSelectedTransportInterface,
    useTransportInterface,
    setUseTransportInterface,
    openMtuConfigModal,
    closeMtuConfigModal,
    saveMtuConfig,
    // Recommendation
    mtuRecommendation,
  };
}
