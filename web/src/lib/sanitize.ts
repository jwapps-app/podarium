/** Sanitiser for publisher-authored show notes.
 *
 *  This is not only an XSS guard. `description_html` is the one field in the API that
 *  still carries publisher markup, and an <img> inside it would make the browser fetch
 *  directly from a publisher CDN -- leaking the viewer's IP and defeating the whole point
 *  of a server-mediated design. So every element that causes a subresource fetch is
 *  dropped, along with every URL-bearing attribute.
 *
 *  Links survive, because a person choosing to click through is their decision, not an
 *  automatic request made on their behalf. They open in a new tab with no referrer.
 */

const ALLOWED_TAGS = new Set([
  "P", "BR", "EM", "I", "STRONG", "B", "U", "CODE", "PRE", "BLOCKQUOTE",
  "UL", "OL", "LI", "A", "H1", "H2", "H3", "H4", "H5", "H6", "SPAN", "DIV",
]);

/** Elements that fetch a subresource, and so must never survive. */
const FETCHING_TAGS = new Set([
  "IMG", "IFRAME", "VIDEO", "AUDIO", "SOURCE", "EMBED", "OBJECT", "SCRIPT",
  "STYLE", "LINK", "PICTURE", "TRACK", "SVG", "USE", "INPUT",
]);

function isSafeHref(value: string): boolean {
  const trimmed = value.trim().toLowerCase();
  return trimmed.startsWith("http://") || trimmed.startsWith("https://") || trimmed.startsWith("mailto:");
}

function scrub(node: Element): void {
  for (const child of Array.from(node.children)) {
    if (FETCHING_TAGS.has(child.tagName)) {
      child.remove();
      continue;
    }

    scrub(child);

    if (!ALLOWED_TAGS.has(child.tagName)) {
      // Unknown but harmless wrapper: keep the text, drop the element.
      child.replaceWith(...Array.from(child.childNodes));
      continue;
    }

    for (const attribute of Array.from(child.attributes)) {
      const name = attribute.name.toLowerCase();
      const keepHref =
        child.tagName === "A" && name === "href" && isSafeHref(attribute.value);
      if (!keepHref) {
        child.removeAttribute(attribute.name);
      }
    }

    if (child.tagName === "A" && child.hasAttribute("href")) {
      child.setAttribute("target", "_blank");
      child.setAttribute("rel", "noopener noreferrer nofollow");
    }
  }
}

export function sanitizeHtml(html: string | null): string {
  if (!html) return "";
  const parsed = new DOMParser().parseFromString(html, "text/html");
  scrub(parsed.body);
  return parsed.body.innerHTML;
}

/** Plain-text version, for one-line summaries where markup would be noise. */
export function toPlainText(html: string | null, limit = 300): string {
  if (!html) return "";
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const text = (parsed.body.textContent ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;
}
