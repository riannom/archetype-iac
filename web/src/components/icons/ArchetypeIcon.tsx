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
    viewBox="0 0 100 100"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    className={className}
    aria-hidden="true"
    role="img"
  >
    <path d="M50 15L85 85H70L50 45L30 85H15L50 15Z" fill={color} />
    <path d="M50 15V45" stroke={color} strokeWidth="2" strokeLinecap="round" opacity="0.35" />
    <circle cx="50" cy="15" r="3" fill={color} opacity="0.35" />
  </svg>
);

export default ArchetypeIcon;
