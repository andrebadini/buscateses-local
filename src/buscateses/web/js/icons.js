/** Inline SVG icons with a consistent 1.75 stroke. */

const SVG_NS = "http://www.w3.org/2000/svg";

/** @type {Record<string, Array<[string, Record<string, string|number>]>>} */
const ICONS = {
  search: [
    ["circle", { cx: 11, cy: 11, r: 7 }],
    ["path", { d: "m16.5 16.5 4.5 4.5" }],
  ],
  sun: [
    ["circle", { cx: 12, cy: 12, r: 4 }],
    [
      "path",
      {
        d: "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
      },
    ],
  ],
  moon: [["path", { d: "M20 15.2A8.4 8.4 0 0 1 8.8 4 8.5 8.5 0 1 0 20 15.2Z" }]],
  menu: [["path", { d: "M4 7h16M4 12h16M4 17h16" }]],
  close: [["path", { d: "m6 6 12 12M18 6 6 18" }]],
  chevronDown: [["path", { d: "m6 9 6 6 6-6" }]],
  chevronLeft: [["path", { d: "m15 6-6 6 6 6" }]],
  chevronRight: [["path", { d: "m9 6 6 6-6 6" }]],
  download: [
    ["path", { d: "M12 3v12" }],
    ["path", { d: "m7 10 5 5 5-5" }],
    ["path", { d: "M4 20h16" }],
  ],
  copy: [
    ["rect", { x: 9, y: 9, width: 11, height: 11, rx: 2 }],
    ["path", { d: "M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" }],
  ],
  bookmark: [["path", { d: "M6 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17l-6-4-6 4V4Z" }]],
  external: [
    ["path", { d: "M14 4h6v6" }],
    ["path", { d: "M20 4 11 13" }],
    ["path", { d: "M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" }],
  ],
  filter: [["path", { d: "M4 5h16l-6 7v6l-4 2v-8L4 5Z" }]],
  upload: [
    ["path", { d: "M12 16V4" }],
    ["path", { d: "m7 9 5-5 5 5" }],
    ["path", { d: "M4 20h16" }],
  ],
  trash: [
    ["path", { d: "M4 7h16" }],
    ["path", { d: "M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" }],
    ["path", { d: "M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" }],
  ],
  alert: [
    ["path", { d: "M12 3 2.8 20h18.4L12 3Z" }],
    ["path", { d: "M12 9v4" }],
    ["path", { d: "M12 17h.01" }],
  ],
  check: [["path", { d: "m4 12 5 5L20 6" }]],
  file: [
    ["path", { d: "M6 3h8l4 4v14H6V3Z" }],
    ["path", { d: "M14 3v4h4" }],
  ],
  folder: [
    ["path", { d: "M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6Z" }],
  ],
  book: [
    ["path", { d: "M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2V5Z" }],
    ["path", { d: "M4 17h15" }],
  ],
  info: [
    ["circle", { cx: 12, cy: 12, r: 9 }],
    ["path", { d: "M12 11v5" }],
    ["path", { d: "M12 8h.01" }],
  ],
  lock: [
    ["rect", { x: 4, y: 10, width: 16, height: 11, rx: 2 }],
    ["path", { d: "M8 10V7a4 4 0 0 1 8 0v3" }],
  ],
  spark: [["path", { d: "M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" }]],
  settings: [
    ["circle", { cx: 12, cy: 12, r: 3 }],
    [
      "path",
      {
        d: "M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4v-.2a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z",
      },
    ],
  ],
};

/**
 * Create an SVG icon.
 *
 * @param {keyof typeof ICONS} name
 * @param {{size?: number, class?: string}} [options]
 * @returns {SVGSVGElement}
 */
export function icon(name, { size = 20, class: className = "" } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  if (className) svg.setAttribute("class", className);
  for (const [tag, attrs] of ICONS[name] ?? []) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) {
      node.setAttribute(key, String(value));
    }
    svg.append(node);
  }
  return svg;
}
