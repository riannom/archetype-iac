
import React, { useMemo, useState, useEffect, useRef, useCallback } from 'react';
import { DeviceModel, AnnotationType, ImageLibraryEntry } from '../types';
import SidebarFilters, { ImageStatus } from './SidebarFilters';
import { useNotifications } from '../../contexts/NotificationContext';
import { getImageDeviceIds } from '../../utils/deviceModels';

interface SidebarProps {
  categories: { name: string; models?: DeviceModel[]; subCategories?: { name: string; models: DeviceModel[] }[] }[];
  onAddDevice: (model: DeviceModel) => void;
  onAddAnnotation: (type: AnnotationType) => void;
  onAddExternalNetwork?: () => void;
  imageLibrary?: ImageLibraryEntry[];
}

const Sidebar: React.FC<SidebarProps> = ({ categories, onAddDevice, onAddAnnotation, onAddExternalNetwork, imageLibrary = [] }) => {
  const { preferences, updateCanvasSettings } = useNotifications();
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    new Set(categories.map(c => c.name))
  );
  const [expandedSubCategories, setExpandedSubCategories] = useState<Set<string>>(
    new Set(categories.flatMap(c => c.subCategories?.map(s => `${c.name}:${s.name}`) || []))
  );

  // Filter state - initialize from defaults, will be populated from preferences
  // Default to 'has_image' to show only devices with images available
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedVendors, setSelectedVendors] = useState<Set<string>>(new Set());
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());
  const [imageStatus, setImageStatus] = useState<ImageStatus>('has_image');

  // Track whether we've loaded initial values from preferences
  const hasLoadedFromPrefs = useRef(false);

  // Load filter settings from preferences on mount
  useEffect(() => {
    if (hasLoadedFromPrefs.current) return;

    const savedFilters = preferences?.canvas_settings?.sidebarFilters;
    if (savedFilters) {
      setSearchQuery(savedFilters.searchQuery || '');
      setSelectedVendors(new Set(savedFilters.selectedVendors || []));
      setSelectedTypes(new Set(savedFilters.selectedTypes || []));
      // Use saved preference, or keep default 'has_image' if not set
      setImageStatus((savedFilters.imageStatus as ImageStatus) || 'has_image');
      hasLoadedFromPrefs.current = true;
    }
  }, [preferences]);

  // Debounced persist function
  const persistFiltersTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const persistFilters = useCallback((filters: {
    searchQuery: string;
    selectedVendors: string[];
    selectedTypes: string[];
    imageStatus: ImageStatus;
  }) => {
    // Clear any pending timeout
    if (persistFiltersTimeoutRef.current) {
      clearTimeout(persistFiltersTimeoutRef.current);
    }

    // Debounce the persistence to avoid too many API calls
    persistFiltersTimeoutRef.current = setTimeout(() => {
      updateCanvasSettings({
        sidebarFilters: filters,
      });
    }, 500);
  }, [updateCanvasSettings]);

  // Persist filters when they change (after initial load)
  useEffect(() => {
    if (!hasLoadedFromPrefs.current) return;

    persistFilters({
      searchQuery,
      selectedVendors: Array.from(selectedVendors),
      selectedTypes: Array.from(selectedTypes),
      imageStatus,
    });
  }, [searchQuery, selectedVendors, selectedTypes, imageStatus, persistFilters]);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (persistFiltersTimeoutRef.current) {
        clearTimeout(persistFiltersTimeoutRef.current);
      }
    };
  }, []);

  // Flatten all devices for filtering
  const allDevices = useMemo(() => {
    const devices: DeviceModel[] = [];
    categories.forEach((cat) => {
      if (cat.models) {
        devices.push(...cat.models);
      }
      if (cat.subCategories) {
        cat.subCategories.forEach((sub) => {
          devices.push(...sub.models);
        });
      }
    });
    return devices;
  }, [categories]);

  // Build device image status map (uses compatible_devices for shared images)
  const deviceImageStatus = useMemo(() => {
    const statusMap = new Map<string, { hasImage: boolean; hasDefault: boolean }>();
    imageLibrary.forEach((img) => {
      getImageDeviceIds(img).forEach((devId) => {
        const existing = statusMap.get(devId) || { hasImage: false, hasDefault: false };
        existing.hasImage = true;
        if (img.is_default) {
          existing.hasDefault = true;
        }
        statusMap.set(devId, existing);
      });
    });
    return statusMap;
  }, [imageLibrary]);

  // Filter devices based on search and filters
  const filterDevice = (device: DeviceModel): boolean => {
    // Search filter
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      const matchesName = device.name.toLowerCase().includes(query);
      const matchesVendor = device.vendor?.toLowerCase().includes(query);
      const matchesId = device.id.toLowerCase().includes(query);
      const matchesTags = device.tags?.some(tag => tag.toLowerCase().includes(query));
      if (!matchesName && !matchesVendor && !matchesId && !matchesTags) {
        return false;
      }
    }

    // Vendor filter
    if (selectedVendors.size > 0 && !selectedVendors.has(device.vendor)) {
      return false;
    }

    // Type filter
    if (selectedTypes.size > 0 && !selectedTypes.has(device.type)) {
      return false;
    }

    // Image status filter
    if (imageStatus !== 'all') {
      const status = deviceImageStatus.get(device.id);
      if (imageStatus === 'has_default' && !status?.hasDefault) return false;
      if (imageStatus === 'has_image' && !status?.hasImage) return false;
      if (imageStatus === 'no_image' && status?.hasImage) return false;
    }

    return true;
  };

  // Filter categories based on active filters
  const filteredCategories = useMemo(() => {
    return categories.map((cat) => {
      if (cat.subCategories) {
        const filteredSubCats = cat.subCategories
          .map((sub) => ({
            ...sub,
            models: sub.models.filter(filterDevice),
          }))
          .filter((sub) => sub.models.length > 0);

        return {
          ...cat,
          subCategories: filteredSubCats,
        };
      } else if (cat.models) {
        return {
          ...cat,
          models: cat.models.filter(filterDevice),
        };
      }
      return cat;
    }).filter((cat) => {
      if (cat.subCategories) return cat.subCategories.length > 0;
      if (cat.models) return cat.models.length > 0;
      return true;
    });
  }, [categories, searchQuery, selectedVendors, selectedTypes, imageStatus, deviceImageStatus]);

  // Count filtered devices per category
  const getCategoryCount = (cat: typeof categories[0]) => {
    if (cat.subCategories) {
      return cat.subCategories.reduce((sum, sub) => sum + sub.models.length, 0);
    }
    return cat.models?.length || 0;
  };

  const toggleCategory = (name: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const toggleSubCategory = (categoryName: string, subName: string) => {
    const key = `${categoryName}:${subName}`;
    setExpandedSubCategories(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const handleVendorToggle = (vendor: string) => {
    setSelectedVendors(prev => {
      const next = new Set(prev);
      if (next.has(vendor)) {
        next.delete(vendor);
      } else {
        next.add(vendor);
      }
      return next;
    });
  };

  const handleTypeToggle = (type: string) => {
    setSelectedTypes(prev => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  const handleClearAll = () => {
    setSearchQuery('');
    setSelectedVendors(new Set());
    setSelectedTypes(new Set());
    setImageStatus('all');
  };

  const getImageStatusIndicator = (deviceId: string) => {
    const status = deviceImageStatus.get(deviceId);
    if (status?.hasDefault) {
      return (
        <span
          className="w-2 h-2 rounded-full bg-emerald-500 shadow-sm"
          title="Has default image"
        />
      );
    }
    if (status?.hasImage) {
      return (
        <span
          className="w-2 h-2 rounded-full bg-blue-500 shadow-sm"
          title="Has images (no default)"
        />
      );
    }
    return (
      <span
        className="w-2 h-2 rounded-full bg-amber-500 shadow-sm"
        title="No images assigned"
      />
    );
  };

  const renderModel = (model: DeviceModel) => (
    <div
      key={model.id}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('application/x-archetype-device', JSON.stringify(model));
        e.dataTransfer.effectAllowed = 'copy';
      }}
      onClick={() => onAddDevice(model)}
      className="group flex items-center p-2 bg-transparent hover:bg-stone-100 dark:hover:bg-stone-800 border border-transparent hover:border-stone-200 dark:hover:border-stone-700 rounded-lg cursor-grab active:cursor-grabbing transition-all"
    >
      <div className="w-8 h-8 rounded bg-white dark:bg-stone-800 flex items-center justify-center mr-3 group-hover:bg-sage-100 dark:group-hover:bg-stone-700 group-hover:text-sage-600 dark:group-hover:text-sage-400 transition-colors border border-stone-200 dark:border-stone-700 shadow-sm relative">
        <i className={`fa-solid ${model.icon} text-xs`}></i>
        {/* Image status indicator */}
        <div className="absolute -top-0.5 -right-0.5">
          {getImageStatusIndicator(model.id)}
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] font-semibold text-stone-700 dark:text-stone-200 truncate group-hover:text-stone-900 dark:group-hover:text-white">{model.name}</div>
        <div className="text-[9px] text-stone-400 dark:text-stone-500 font-medium truncate italic">{model.versions[0]}</div>
      </div>
      <div className="opacity-0 group-hover:opacity-100 transition-opacity">
        <i className="fa-solid fa-plus-circle text-xs text-sage-500"></i>
      </div>
    </div>
  );

  const annotationTools: { type: AnnotationType, icon: string, label: string }[] = [
    { type: 'text', icon: 'fa-font', label: 'Label' },
    { type: 'rect', icon: 'fa-square', label: 'Box' },
    { type: 'circle', icon: 'fa-circle', label: 'Zone' },
    { type: 'arrow', icon: 'fa-arrow-right', label: 'Flow' },
    { type: 'caption', icon: 'fa-comment', label: 'Note' },
  ];

  // External network connection tool
  const externalNetworkTool = {
    icon: 'fa-cloud',
    label: 'External Network',
  };

  return (
    <div className="w-64 bg-white/40 dark:bg-stone-900/40 backdrop-blur-md border-r border-stone-200 dark:border-stone-800 flex flex-col h-full overflow-hidden">
      <div className="p-4 border-b border-stone-200 dark:border-stone-800 bg-stone-50/50 dark:bg-stone-800/30">
        <h2 className="text-sm font-bold uppercase tracking-wider text-stone-500 dark:text-stone-400 flex items-center gap-2">
          <i className="fa-solid fa-boxes-stacked text-sage-600 dark:text-sage-500"></i>
          Library
        </h2>
      </div>

      {/* Filters */}
      <SidebarFilters
        devices={allDevices}
        imageLibrary={imageLibrary}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        selectedVendors={selectedVendors}
        onVendorToggle={handleVendorToggle}
        selectedTypes={selectedTypes}
        onTypeToggle={handleTypeToggle}
        imageStatus={imageStatus}
        onImageStatusChange={setImageStatus}
        onClearAll={handleClearAll}
      />

      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {/* External Networks Section */}
        {onAddExternalNetwork && (
          <div className="mb-4">
            <div className="px-4 py-2 flex items-center justify-between text-[10px] font-bold text-stone-400 dark:text-stone-500 uppercase tracking-widest bg-stone-100 dark:bg-stone-800 border-y border-stone-200 dark:border-stone-800 sticky top-0 z-10">
              <span>Connectivity</span>
            </div>
            <div className="p-2">
              <button
                onClick={onAddExternalNetwork}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/x-archetype-external', 'true');
                  e.dataTransfer.effectAllowed = 'copy';
                }}
                className="w-full flex items-center p-3 rounded-lg bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-950/30 dark:to-purple-950/30 hover:from-blue-100 hover:to-purple-100 dark:hover:from-blue-900/40 dark:hover:to-purple-900/40 border border-blue-200 dark:border-blue-800/50 hover:border-blue-300 dark:hover:border-blue-700 transition-all gap-3 group shadow-sm cursor-grab active:cursor-grabbing"
              >
                <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-purple-500 flex items-center justify-center shadow-md">
                  <i className={`fa-solid ${externalNetworkTool.icon} text-white text-sm`}></i>
                </div>
                <div className="flex-1 text-left">
                  <div className="text-[11px] font-bold text-stone-700 dark:text-stone-200">{externalNetworkTool.label}</div>
                  <div className="text-[9px] text-stone-500 dark:text-stone-400">Connect to VLAN or bridge</div>
                </div>
                <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                  <i className="fa-solid fa-plus-circle text-blue-500 dark:text-blue-400"></i>
                </div>
              </button>
            </div>
          </div>
        )}

        {/* Tools Section */}
        <div className="mb-4">
          <div className="px-4 py-2 flex items-center justify-between text-[10px] font-bold text-stone-400 dark:text-stone-500 uppercase tracking-widest bg-stone-100 dark:bg-stone-800 border-y border-stone-200 dark:border-stone-800 sticky top-0 z-10">
            <span>Annotations</span>
          </div>
          <div className="p-2 grid grid-cols-2 gap-2">
            {annotationTools.map(tool => (
              <button
                key={tool.type}
                onClick={() => onAddAnnotation(tool.type)}
                className="flex flex-col items-center justify-center p-2 rounded-lg bg-white dark:bg-stone-800/50 hover:bg-stone-50 dark:hover:bg-stone-800 border border-stone-200 dark:border-stone-700 hover:border-sage-300 dark:hover:border-sage-500/50 transition-all gap-1 group shadow-sm"
              >
                <i className={`fa-solid ${tool.icon} text-stone-400 group-hover:text-sage-600 dark:group-hover:text-sage-500 text-xs`}></i>
                <span className="text-[9px] text-stone-500 dark:text-stone-500 group-hover:text-stone-700 dark:group-hover:text-stone-200 font-bold">{tool.label}</span>
              </button>
            ))}
          </div>
        </div>

        {filteredCategories.map((category) => (
          <div key={category.name} className="mb-2">
            <button
              onClick={() => toggleCategory(category.name)}
              className="w-full px-4 py-2 flex items-center justify-between text-[10px] font-bold text-stone-400 dark:text-stone-500 uppercase tracking-widest bg-stone-100 dark:bg-stone-800 border-y border-stone-200 dark:border-stone-800 sticky top-0 z-10 hover:bg-stone-200 dark:hover:bg-stone-700 transition-colors"
            >
              <span className="flex items-center gap-2">
                {category.name}
                <span className="text-[9px] font-normal text-stone-400 dark:text-stone-600">
                  ({getCategoryCount(category)})
                </span>
              </span>
              <i className={`fa-solid fa-chevron-down text-[8px] transition-transform duration-200 ${
                expandedCategories.has(category.name) ? '' : '-rotate-90'
              }`}></i>
            </button>

            <div className={`overflow-hidden transition-all duration-200 ${
              expandedCategories.has(category.name)
                ? 'max-h-[2000px] opacity-100'
                : 'max-h-0 opacity-0'
            }`}>
              <div className="p-1 space-y-1">
                {category.subCategories ? (
                  category.subCategories.map(sub => (
                    <div key={sub.name} className="mt-2">
                      <button
                        onClick={() => toggleSubCategory(category.name, sub.name)}
                        className="w-full px-3 py-1 text-[9px] font-bold text-stone-400 dark:text-stone-600 uppercase flex items-center gap-2 hover:text-stone-600 dark:hover:text-stone-400 transition-colors"
                      >
                        <div className="h-px flex-1 bg-stone-200 dark:bg-stone-800"></div>
                        <span className="flex items-center gap-1">
                          {sub.name}
                          <span className="font-normal text-stone-400 dark:text-stone-600">
                            ({sub.models.length})
                          </span>
                          <i className={`fa-solid fa-chevron-down text-[7px] transition-transform duration-200 ${
                            expandedSubCategories.has(`${category.name}:${sub.name}`) ? '' : '-rotate-90'
                          }`}></i>
                        </span>
                        <div className="h-px flex-1 bg-stone-200 dark:bg-stone-800"></div>
                      </button>
                      <div className={`overflow-hidden transition-all duration-200 ${
                        expandedSubCategories.has(`${category.name}:${sub.name}`)
                          ? 'max-h-[1000px] opacity-100'
                          : 'max-h-0 opacity-0'
                      }`}>
                        <div className="space-y-1 mt-1 px-1">
                          {sub.models.map(renderModel)}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="px-1 pt-1">
                    {category.models?.map(renderModel)}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}

        {filteredCategories.length === 0 && (
          <div className="p-4 text-center">
            <i className="fa-solid fa-search text-2xl text-stone-300 dark:text-stone-700 mb-2" />
            <p className="text-xs text-stone-500 dark:text-stone-400">
              No devices match your filters
            </p>
            <button
              onClick={handleClearAll}
              className="mt-2 text-[10px] font-bold text-sage-600 hover:text-sage-500"
            >
              Clear filters
            </button>
          </div>
        )}
      </div>

      <div className="p-4 border-t border-stone-200 dark:border-stone-800 bg-stone-50 dark:bg-stone-950/50">
        <div className="flex flex-col gap-2 p-3 bg-sage-500/5 border border-sage-500/10 rounded-lg">
          <div className="flex items-center gap-2 text-[10px] font-bold text-sage-600 dark:text-sage-400 uppercase tracking-tight">
            <i className="fa-solid fa-lightbulb"></i>
            <span>Network Canvas</span>
          </div>
          <p className="text-[9px] text-stone-500 dark:text-stone-400 leading-relaxed">
            Click or drag devices onto the grid to build your topology.
          </p>
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
