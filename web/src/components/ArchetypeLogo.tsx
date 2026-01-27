type ArchetypeLogoProps = {
  className?: string;
};

export function ArchetypeLogo({ className }: ArchetypeLogoProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      aria-hidden="true"
      role="img"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <polygon points="32,8 52,32 32,56 12,32" fill="none" stroke="currentColor" strokeWidth="2.5"/>
      <line x1="32" y1="8" x2="32" y2="56" stroke="currentColor" strokeWidth="2" opacity="0.4"/>
      <line x1="12" y1="32" x2="52" y2="32" stroke="currentColor" strokeWidth="2" opacity="0.4"/>
    </svg>
  );
}
