/** Small DOM helpers. All text goes through textContent, never innerHTML. */

const PROPERTIES = new Set([
  "value",
  "checked",
  "disabled",
  "selected",
  "open",
  "hidden",
  "indeterminate",
  "multiple",
]);

/**
 * Create an element.
 *
 * @param {string} tag
 * @param {Record<string, unknown> | null} [attrs]
 * @param {...unknown} children
 * @returns {HTMLElement}
 */
export function h(tag, attrs, ...children) {
  const element = document.createElement(tag);
  applyAttrs(element, attrs);
  appendChildren(element, children);
  return element;
}

/** Apply attributes, properties, dataset, style and event listeners. */
export function applyAttrs(element, attrs) {
  if (!attrs) return;
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") {
      element.className = String(value);
      continue;
    }
    if (key === "style" && typeof value === "object") {
      Object.assign(element.style, value);
      continue;
    }
    if (key === "dataset" && typeof value === "object") {
      Object.assign(element.dataset, value);
      continue;
    }
    if (key === "text") {
      element.textContent = String(value);
      continue;
    }
    if (key.startsWith("on") && typeof value === "function") {
      element.addEventListener(key.slice(2).toLowerCase(), value);
      continue;
    }
    if (PROPERTIES.has(key)) {
      element[key] = value;
      continue;
    }
    element.setAttribute(key, value === true ? "" : String(value));
  }
}

/** Append strings as safe text nodes and nodes as-is. */
export function appendChildren(element, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Build a fragment from the same child rules as {@link h}. */
export function fragment(...children) {
  const fragmentNode = document.createDocumentFragment();
  appendChildren(fragmentNode, children);
  return fragmentNode;
}

/** Remove every child of an element. */
export function clear(element) {
  element.replaceChildren();
}

/**
 * Turn `[[termo]]` markers into <mark> nodes without ever parsing HTML.
 *
 * @param {unknown} value
 * @returns {DocumentFragment}
 */
export function highlighted(value) {
  // Snippets come from PDFs: paragraph breaks become a single space so no
  // visible "\n" leaks into the rendered text.
  const text = String(value ?? "").replace(/\s*\n+\s*/g, " ");
  const output = document.createDocumentFragment();
  const pattern = /\[\[([\s\S]*?)\]\]/g;
  let lastIndex = 0;
  let match = pattern.exec(text);
  while (match) {
    if (match.index > lastIndex) {
      output.append(document.createTextNode(text.slice(lastIndex, match.index)));
    }
    output.append(h("mark", {}, match[1]));
    lastIndex = match.index + match[0].length;
    match = pattern.exec(text);
  }
  if (lastIndex < text.length) {
    output.append(document.createTextNode(text.slice(lastIndex)));
  }
  return output;
}

/** Return a safe http(s) URL or null. */
export function safeUrl(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return /^https?:/i.test(raw) ? raw : parsed.href;
    }
  } catch {
    return null;
  }
  return null;
}
