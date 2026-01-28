import React from 'react';

interface ArchetypeIconProps {
  size?: number;
  color?: string;
  className?: string;
}

export const ArchetypeIcon: React.FC<ArchetypeIconProps> = ({
  size = 64,
  color = 'currentColor',
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 64 64"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    className={className}
  >
    <polygon points="32,8 52,32 32,56 12,32" fill="none" stroke={color} strokeWidth="2.5"/>
    <line x1="32" y1="8" x2="32" y2="56" stroke={color} strokeWidth="2" opacity="0.4"/>
    <line x1="12" y1="32" x2="52" y2="32" stroke={color} strokeWidth="2" opacity="0.4"/>
  </svg>
);

export default ArchetypeIcon;
