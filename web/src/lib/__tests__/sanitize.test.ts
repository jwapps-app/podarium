import { describe, expect, it } from "vitest";

import { sanitizeHtml, toPlainText } from "../sanitize";

/** Show notes are the one place publisher-authored markup reaches the page. An <img> in
 *  there would have the browser fetch straight from a publisher CDN, leaking the viewer's
 *  IP and defeating the point of a server-mediated design -- so these are privacy tests as
 *  much as XSS ones. */
describe("sanitizeHtml", () => {
  it("strips every element that would fetch a subresource", () => {
    const hostile = `
      <p>Real note</p>
      <img src="https://cdn.publisher.example/tracker.gif" />
      <iframe src="https://publisher.example/embed"></iframe>
      <video src="https://publisher.example/v.mp4"></video>
      <picture><source srcset="https://publisher.example/a.webp" /></picture>
      <object data="https://publisher.example/o"></object>
      <embed src="https://publisher.example/e" />
    `;
    const clean = sanitizeHtml(hostile);

    expect(clean).toContain("Real note");
    for (const fragment of ["img", "iframe", "video", "source", "object", "embed", "publisher.example"]) {
      expect(clean).not.toContain(fragment);
    }
  });

  it("removes scripts and inline event handlers", () => {
    const clean = sanitizeHtml(
      `<p onclick="steal()">text</p><script>steal()</script><div onerror="x">more</div>`,
    );

    expect(clean).not.toContain("script");
    expect(clean).not.toContain("onclick");
    expect(clean).not.toContain("onerror");
    expect(clean).toContain("text");
    expect(clean).toContain("more");
  });

  it("keeps links but hardens them", () => {
    const clean = sanitizeHtml(`<a href="https://example.com/show">Sponsor</a>`);

    expect(clean).toContain('href="https://example.com/show"');
    expect(clean).toContain('target="_blank"');
    expect(clean).toContain("noopener");
    expect(clean).toContain("noreferrer");
  });

  it("drops javascript: and data: hrefs", () => {
    for (const href of ["javascript:alert(1)", "data:text/html,<script>x</script>", "vbscript:x"]) {
      const clean = sanitizeHtml(`<a href="${href}">click</a>`);
      expect(clean).not.toContain("href");
      expect(clean).toContain("click");
    }
  });

  it("unwraps unknown elements instead of dropping their text", () => {
    const clean = sanitizeHtml(`<article><p>kept</p></article>`);
    expect(clean).toContain("kept");
    expect(clean).not.toContain("article");
  });

  it("strips styling attributes that could be used to hide or reposition content", () => {
    const clean = sanitizeHtml(`<p style="position:fixed;top:0" class="x" id="y">note</p>`);
    expect(clean).toBe("<p>note</p>");
  });

  it("handles null and empty input", () => {
    expect(sanitizeHtml(null)).toBe("");
    expect(sanitizeHtml("")).toBe("");
  });
});

describe("toPlainText", () => {
  it("flattens markup and collapses whitespace", () => {
    expect(toPlainText("<p>One</p>\n  <p>Two</p>")).toBe("One Two");
  });

  it("truncates at the limit", () => {
    const long = toPlainText(`<p>${"word ".repeat(200)}</p>`, 20);
    expect(long.length).toBeLessThanOrEqual(21);
    expect(long.endsWith("…")).toBe(true);
  });
});
