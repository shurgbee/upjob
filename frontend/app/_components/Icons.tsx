import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...props}>
      {children}
    </svg>
  );
}

export function MenuIcon(props: IconProps) {
  return <IconBase {...props}><path d="M4 7h16M4 12h16M4 17h16" /></IconBase>;
}

export function CloseIcon(props: IconProps) {
  return <IconBase {...props}><path d="m6 6 12 12M18 6 6 18" /></IconBase>;
}

export function BellIcon(props: IconProps) {
  return <IconBase {...props}><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></IconBase>;
}

export function SunIcon(props: IconProps) {
  return <IconBase {...props}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></IconBase>;
}

export function MoonIcon(props: IconProps) {
  return <IconBase {...props}><path d="M20.4 15.2A8.5 8.5 0 0 1 8.8 3.6 8.5 8.5 0 1 0 20.4 15.2Z" /></IconBase>;
}

export function CoinIcon(props: IconProps) {
  return <IconBase {...props}><circle cx="12" cy="12" r="8.5" /><path d="M14.7 8.5h-3.4a2 2 0 0 0 0 4h1.4a2 2 0 0 1 0 4H9.3M12 6.5v2M12 16.5v2" /></IconBase>;
}

export function BriefcaseIcon(props: IconProps) {
  return <IconBase {...props}><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18M10 12v2h4v-2" /></IconBase>;
}

export function DocumentIcon(props: IconProps) {
  return <IconBase {...props}><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 13h6M9 17h6" /></IconBase>;
}

export function GiftIcon(props: IconProps) {
  return <IconBase {...props}><path d="M4 10h16v11H4zM2.5 6h19v4h-19zM12 6v15M12 6H8.8a2.3 2.3 0 1 1 2.3-2.3L12 6Zm0 0h3.2a2.3 2.3 0 1 0-2.3-2.3L12 6Z" /></IconBase>;
}

export function SparkIcon(props: IconProps) {
  return <IconBase {...props}><path d="m12 3 1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4L12 3ZM18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z" /></IconBase>;
}

export function ArrowIcon(props: IconProps) {
  return <IconBase {...props}><path d="M5 12h14M14 7l5 5-5 5" /></IconBase>;
}

export function FlameIcon(props: IconProps) {
  return <IconBase {...props}><path d="M13.5 3.5c.8 3-1.9 4.4-1.9 7 0 1.5 1 2.2 2 2.2 1.8 0 2.9-1.6 2.5-4.2 2.2 1.8 3.4 4.3 2.6 7-1 3.4-3.8 5.5-7.2 5.5-4.2 0-7.2-3-7.2-7 0-3.2 1.8-6 4.8-8-.2 2.7.8 4.3 2 4.3 1.6 0 3-2.4 2.4-6.8Z" /></IconBase>;
}
