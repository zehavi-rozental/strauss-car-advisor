// A small set of line icons drawn for this product (1.6px stroke, 24px grid). Decorative: the button or the text
// next to an icon always carries the name.
import type { ReactNode } from 'react'

const P: Record<string, ReactNode> = {
  send: <path d="M20 12 5 5l2.5 7L5 19l15-7Zm-12.5 0H13" />,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2.5" /><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5" /></>,
  check: <path d="m5 12.5 4.2 4.2L19 7" />,
  retry: <><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" /><path d="M19.6 4.5v4.2h-4.2" /></>,
  edit: <><path d="M14.5 5.5 18.5 9.5" /><path d="M5 19l.9-3.9 9.9-9.9a1.8 1.8 0 0 1 2.6 0l1.4 1.4a1.8 1.8 0 0 1 0 2.6l-9.9 9.9L5 19Z" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  chevron: <path d="m14.5 6-6 6 6 6" />,
  chevronDown: <path d="m6 9.5 6 6 6-6" />,
  upload: <><path d="M12 15V4.5" /><path d="m7.5 9 4.5-4.5L16.5 9" /><path d="M5 15.5v2A2.5 2.5 0 0 0 7.5 20h9a2.5 2.5 0 0 0 2.5-2.5v-2" /></>,
  doc: <><path d="M14 3.5H7.5A2 2 0 0 0 5.5 5.5v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8Z" /><path d="M14 3.5V8h4.5M9 13h6M9 16.5h4" /></>,
  form: <><rect x="4.5" y="4.5" width="15" height="15" rx="3" /><path d="M8 9h8M8 12.5h8M8 16h5" /></>,
  grid: <><rect x="4.5" y="4.5" width="6" height="6" rx="1.5" /><rect x="13.5" y="4.5" width="6" height="6" rx="1.5" /><rect x="4.5" y="13.5" width="6" height="6" rx="1.5" /><rect x="13.5" y="13.5" width="6" height="6" rx="1.5" /></>,
  table: <><rect x="4" y="5" width="16" height="14" rx="2.5" /><path d="M4 10h16M4 14.5h16M10 10v9" /></>,
  swap: <><path d="M7 8h11.5M15 4.5 18.5 8 15 11.5" /><path d="M17 16H5.5M9 12.5 5.5 16 9 19.5" /></>,
  lock: <><rect x="5.5" y="10.5" width="13" height="9.5" rx="2.5" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" /></>,
  user: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 19.5a7 7 0 0 1 14 0" /></>,
  menu: <path d="M5 8h14M5 12h14M5 16h14" />,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5.5M12 7.8v.4" /></>,
  alert: <><path d="M12 4.5 20.5 19h-17L12 4.5Z" /><path d="M12 10v4.5M12 17v.2" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  bolt: <path d="M13 3.5 6.5 13.5h5L11 20.5l6.5-10h-5l.5-7Z" />,
  leaf: <><path d="M5 19C5 10 11 5 19.5 4.5 19 13 14 19 5 19Z" /><path d="M5 19 13 11" /></>,
  plug: <><path d="M9 4v4M15 4v4" /><path d="M6.5 8h11v3a5.5 5.5 0 0 1-11 0V8Z" /><path d="M12 16.5V20" /></>,
  drop: <path d="M12 4s5.5 6.2 5.5 10a5.5 5.5 0 0 1-11 0C6.5 10.2 12 4 12 4Z" />,
  road: <><path d="M8.5 4 5 20M15.5 4 19 20" /><path d="M12 5v2.5M12 11v2.5M12 17v2.5" /></>,
  minus: <path d="M6 12h12" />,
  trendUp: <><path d="M4 16.5 9.5 11l3.5 3.5L20 7.5" /><path d="M15 7.5h5v5" /></>,
  trendDown: <><path d="M4 7.5 9.5 13l3.5-3.5L20 16.5" /><path d="M15 16.5h5v-5" /></>,
  arrowLeft: <><path d="M19 12H5.5" /><path d="m11 6-6 6 6 6" /></>,
  rep: <><circle cx="9" cy="8.5" r="3" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0" /><path d="M15 6.5h5.5M15 10h5.5M17 13.5h3.5" /></>,
  motion: <><path d="M4 12c2.5-4 5.5-4 8 0s5.5 4 8 0" /><path d="M4 17c2.5-4 5.5-4 8 0s5.5 4 8 0" opacity="0.5" /></>,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="3" /></>,
  eyeOff: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="3" /><path d="M4 4l16 16" /></>,
  logout: <><path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H10" /><path d="M14 8.5 10.5 12l3.5 3.5M10.5 12H20" /></>,
}

export type IconName = keyof typeof P

export function Icon({ name, size = 20, className }: { name: IconName | string; size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false"
         className={className ? `ico ${className}` : 'ico'} fill="none" stroke="currentColor" strokeWidth={1.6}
         strokeLinecap="round" strokeLinejoin="round">
      {P[name]}
    </svg>
  )
}
