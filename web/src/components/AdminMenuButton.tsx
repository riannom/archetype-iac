import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../contexts/UserContext';
import { canManageImages, canManageUsers, canViewInfrastructure } from '../utils/permissions';

type AdminNavItem = {
  label: string;
  path: string;
  icon: string;
  title: string;
};

export default function AdminMenuButton() {
  const { user } = useUser();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const canShowAdmin = canViewInfrastructure(user ?? null);
  const showUsers = canManageUsers(user ?? null);
  const showNodes = canManageImages(user ?? null);

  useEffect(() => {
    const handleOutsideClick = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    if (open) {
      document.addEventListener('mousedown', handleOutsideClick);
    }

    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
    };
  }, [open]);

  if (!canShowAdmin) {
    return null;
  }

  const items: AdminNavItem[] = [
    {
      label: 'Settings',
      path: '/admin/settings',
      icon: 'fa-sliders',
      title: 'Global Settings',
    },
    {
      label: 'Infrastructure',
      path: '/infrastructure',
      icon: 'fa-network-wired',
      title: 'Infrastructure Settings',
    },
  ];

  if (showNodes) {
    items.push({
      label: 'Nodes',
      path: '/nodes',
      icon: 'fa-microchip',
      title: 'Manage Nodes',
    });
  }

  if (showUsers) {
    items.push({
      label: 'Users',
      path: '/admin/users',
      icon: 'fa-users',
      title: 'User Management',
    });
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-2 px-3 py-2 glass-control text-stone-600 dark:text-stone-300 rounded-lg transition-all"
        title="Admin menu"
      >
        <i className="fa-solid fa-shield-halved text-xs"></i>
        <span className="text-[10px] font-bold uppercase">Admin</span>
        <i className={`fa-solid fa-chevron-${open ? 'up' : 'down'} text-[9px]`}></i>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-56 glass-surface-elevated border border-stone-200 dark:border-black/80 rounded-lg shadow-lg overflow-hidden z-50">
          {items.map((item, index) => (
            <button
              key={item.path}
              onClick={() => {
                navigate(item.path);
                setOpen(false);
              }}
              className={`w-full px-3 py-2 text-left text-xs text-stone-700 dark:text-stone-100 hover:bg-stone-50 dark:hover:bg-black/80 flex items-center gap-2 ${
                index > 0 ? 'border-t border-stone-100 dark:border-black/70' : ''
              }`}
              title={item.title}
            >
              <i className={`fa-solid ${item.icon} text-sage-600 dark:text-sage-400 w-4`}></i>
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
