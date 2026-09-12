import type { CSSProperties } from "react";

const paths: Record<string, string> = {
  mission: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  vision: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  graph: "M4 3h6v5H4z M14 16h6v5h-6z M14 3h6v5h-6z M7 8v10h7 M10 5.5h4",
  timeline: "M7 3v18 M3 5h8 M12 5h8 M3 12h8 M12 12h6 M3 19h8 M12 19h8",
  memory: "M4 6c0-4 16-4 16 0s-16 4-16 0v6c0 4 16 4 16 0V6 M4 12v6c0 4 16 4 16 0v-6",
  models: "M12 2l9 5v10l-9 5-9-5V7z M3 7l9 5 9-5 M12 12v10",
  security: "M12 2l8 4v6c0 6-8 10-8 10S4 18 4 12V6z M8 12l3 3 5-6",
  resources: "M3 12h4l3-8 4 16 3-8h4",
  recovery: "M3 11a9 9 0 1 1 2 7 M3 4v7h7 M12 7v6l4 2",
  search: "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0 M15 15l6 6",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  plus: "M12 5v14 M5 12h14",
  play: "M7 4l14 8-14 8z",
  pause: "M8 5v14 M16 5v14",
  stop: "M6 6h12v12H6z",
  check: "M5 12l4 4L19 6",
  chevron: "M9 5l7 7-7 7",
  close: "M6 6l12 12 M6 18L18 6",
  moon: "M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10z",
  sun: "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1 1 M18 18l1 1 M5 19l1-1 M18 6l1-1",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  download: "M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5",
  file: "M5 2h9l5 5v15H5z M14 2v6h5 M8 12h8 M8 16h8",
  code: "M8 5l-6 7 6 7 M16 5l6 7-6 7 M14 3l-4 18",
  external: "M14 3h7v7 M21 3L10 14 M10 4H4v16h16v-6",
  bell: "M5 17h14l-2-3V9a5 5 0 0 0-10 0v5z M10 21h4",
  terminal: "M3 4h18v16H3z M7 8l4 4-4 4 M13 16h4",
  info: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 11v6 M12 7v1",
  layers: "M12 3l10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5"
};

export function Icon({ name, size = 18, style, className }: { name: string; size?: number; style?: CSSProperties; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style} className={className}><path d={paths[name] ?? paths.mission} /></svg>;
}
