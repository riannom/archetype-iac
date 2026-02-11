/**
 * Tests for hardware spec pipeline in the frontend.
 *
 * Validates that:
 * 1. DeviceNode type includes hardware spec fields
 * 2. GraphNode type includes hardware spec fields for topology serialization
 * 3. DeviceModel type includes driver fields from vendor API
 */
import { describe, it, expect } from 'vitest';
import type { DeviceNode, DeviceModel, DeviceType } from './types';
import type { GraphNode } from '../types';

describe('Hardware spec types', () => {
  describe('DeviceNode', () => {
    it('should accept all hardware spec fields', () => {
      const node: DeviceNode = {
        id: 'test-1',
        nodeType: 'device',
        name: 'cat9000v-uadp-1',
        type: 'router' as DeviceType,
        model: 'cat9000v-uadp',
        version: '17.12.1',
        x: 100,
        y: 200,
        cpu: 4,
        memory: 18432,
        disk_driver: 'ide',
        nic_driver: 'e1000',
        machine_type: 'pc-i440fx-6.2',
      };
      expect(node.cpu).toBe(4);
      expect(node.memory).toBe(18432);
      expect(node.disk_driver).toBe('ide');
      expect(node.nic_driver).toBe('e1000');
      expect(node.machine_type).toBe('pc-i440fx-6.2');
    });

    it('should work without optional hardware fields', () => {
      const node: DeviceNode = {
        id: 'test-2',
        nodeType: 'device',
        name: 'ceos-1',
        type: 'switch' as DeviceType,
        model: 'ceos',
        version: '4.30.0F',
        x: 100,
        y: 200,
      };
      expect(node.cpu).toBeUndefined();
      expect(node.memory).toBeUndefined();
      expect(node.disk_driver).toBeUndefined();
    });
  });

  describe('GraphNode', () => {
    it('should include hardware spec fields for topology serialization', () => {
      const graphNode: GraphNode = {
        id: 'test-1',
        name: 'cat9000v-uadp-1',
        device: 'cat9000v-uadp',
        version: '17.12.1',
        cpu: 4,
        memory: 18432,
        disk_driver: 'ide',
        nic_driver: 'e1000',
        machine_type: 'pc-i440fx-6.2',
      };
      expect(graphNode.cpu).toBe(4);
      expect(graphNode.memory).toBe(18432);
      expect(graphNode.disk_driver).toBe('ide');
      expect(graphNode.nic_driver).toBe('e1000');
      expect(graphNode.machine_type).toBe('pc-i440fx-6.2');
    });

    it('should allow null hardware spec fields', () => {
      const graphNode: GraphNode = {
        id: 'test-2',
        name: 'ceos-1',
        cpu: null,
        memory: null,
        disk_driver: null,
        nic_driver: null,
        machine_type: null,
      };
      expect(graphNode.cpu).toBeNull();
    });

    it('should work without hardware fields', () => {
      const graphNode: GraphNode = {
        id: 'test-3',
        name: 'linux-1',
      };
      expect(graphNode.cpu).toBeUndefined();
      expect(graphNode.memory).toBeUndefined();
    });
  });

  describe('DeviceModel', () => {
    it('should include driver fields from vendor API', () => {
      const model: DeviceModel = {
        id: 'iosv',
        type: 'router' as DeviceType,
        name: 'Cisco IOSv',
        icon: 'fa-server',
        versions: ['15.9(3)M'],
        isActive: true,
        vendor: 'Cisco',
        memory: 512,
        cpu: 1,
        diskDriver: 'ide',
        nicDriver: 'e1000',
        machineType: 'pc-i440fx-6.2',
      };
      expect(model.diskDriver).toBe('ide');
      expect(model.nicDriver).toBe('e1000');
      expect(model.machineType).toBe('pc-i440fx-6.2');
    });
  });

  describe('Hardware spec serialization', () => {
    it('should serialize DeviceNode to GraphNode with hardware specs', () => {
      const deviceNode: DeviceNode = {
        id: 'test-1',
        nodeType: 'device',
        name: 'cat9000v-1',
        type: 'router' as DeviceType,
        model: 'cat9000v-uadp',
        version: '17.12.1',
        x: 100,
        y: 200,
        cpu: 4,
        memory: 18432,
        disk_driver: 'ide',
        nic_driver: 'e1000',
        machine_type: 'pc-i440fx-6.2',
      };

      // Simulate saveTopology serialization
      const graphNode: GraphNode = {
        id: deviceNode.id,
        name: deviceNode.name,
        device: deviceNode.model,
        version: deviceNode.version,
        cpu: deviceNode.cpu,
        memory: deviceNode.memory,
        disk_driver: deviceNode.disk_driver,
        nic_driver: deviceNode.nic_driver,
        machine_type: deviceNode.machine_type,
      };

      expect(graphNode.cpu).toBe(4);
      expect(graphNode.memory).toBe(18432);
      expect(graphNode.disk_driver).toBe('ide');
      expect(graphNode.nic_driver).toBe('e1000');
      expect(graphNode.machine_type).toBe('pc-i440fx-6.2');
    });

    it('should use model defaults when creating node from DeviceModel', () => {
      const model: DeviceModel = {
        id: 'cat9000v-uadp',
        type: 'router' as DeviceType,
        name: 'Cisco Cat9000v UADP',
        icon: 'fa-server',
        versions: ['17.12.1'],
        isActive: true,
        vendor: 'Cisco',
        memory: 18432,
        cpu: 4,
        diskDriver: 'ide',
        nicDriver: 'e1000',
        machineType: 'pc-i440fx-6.2',
      };

      // Simulate handleAddDevice
      const newNode: DeviceNode = {
        id: 'test-1',
        nodeType: 'device',
        name: 'CAT9000V-UADP-1',
        type: model.type,
        model: model.id,
        version: model.versions[0],
        x: 300,
        y: 200,
        cpu: model.cpu || 1,
        memory: model.memory || 1024,
        disk_driver: model.diskDriver,
        nic_driver: model.nicDriver,
        machine_type: model.machineType,
      };

      expect(newNode.cpu).toBe(4);
      expect(newNode.memory).toBe(18432);
      expect(newNode.disk_driver).toBe('ide');
      expect(newNode.nic_driver).toBe('e1000');
      expect(newNode.machine_type).toBe('pc-i440fx-6.2');
    });

    it('should fall back to defaults when model has no hardware specs', () => {
      const model: DeviceModel = {
        id: 'linux',
        type: 'host' as DeviceType,
        name: 'Linux',
        icon: 'fa-linux',
        versions: ['latest'],
        isActive: true,
        vendor: 'Generic',
      };

      const newNode: DeviceNode = {
        id: 'test-2',
        nodeType: 'device',
        name: 'LINUX-1',
        type: model.type,
        model: model.id,
        version: model.versions[0],
        x: 300,
        y: 200,
        cpu: model.cpu || 1,
        memory: model.memory || 1024,
        disk_driver: model.diskDriver,
        nic_driver: model.nicDriver,
        machine_type: model.machineType,
      };

      expect(newNode.cpu).toBe(1);
      expect(newNode.memory).toBe(1024);
      expect(newNode.disk_driver).toBeUndefined();
    });
  });
});
