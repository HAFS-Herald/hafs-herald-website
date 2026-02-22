// Loads content from the backend CMS if available.
// If the backend isn't running, falls back to local ./articles.js.
//
// Media (cover images + inline markdown image blocks) are stored as relative paths
// like /assets/uploads/<file>. When rendering on Cloudflare Pages, those paths
// must be resolved against the backend (Railway) origin, otherwise Pages will
// return index.html for missing assets.

let _cache = null;

function apiBase() {
  const raw = window.HAFS_API_BASE || "";
  return String(raw).replace(/\/+$/, "");
}

function isAbsoluteUrl(u) {
  return /^(https?:)?\/\//i.test(u) || /^data:/i.test(u) || /^blob:/i.test(u);
}

function resolveMediaUrl(u) {
  const s = String(u || "").trim();
  if (!s) return "";
  if (isAbsoluteUrl(s)) return s;

  const base = apiBase();
  // If API base is blank, treat paths as same-origin.
  if (!base) return s;

  if (s.startsWith("/")) return `${base}${s}`;
  return `${base}/${s}`;
}

function rewriteBodyParagraph(p) {
  const s = String(p || "").trim();
  // Image block syntax (one paragraph):
  // ![alt text](path/to/image.jpg "Optional caption")
  const m = s.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]+)\")?\)$/);
  if (!m) return s;

  const alt = m[1] || "";
  const src = resolveMediaUrl(m[2] || "");
  const cap = m[3];
  if (cap != null) return `![${alt}](${src} "${cap}")`;
  return `![${alt}](${src})`;
}

export async function loadContent() {
  if (_cache) return _cache;

  // Try backend first
  try {
    const API = apiBase();
    const r = await fetch(`${API}/api/content`, { cache: "no-store" });
    if (!r.ok) throw new Error("Backend unavailable");
    const data = await r.json();

    const articles = (data.articles || []).map((a) => {
      const copy = { ...a };
      if (copy.cover) copy.cover = resolveMediaUrl(copy.cover);
      if (Array.isArray(copy.body)) copy.body = copy.body.map(rewriteBodyParagraph);
      return copy;
    });

    _cache = {
      sections: data.sections || [],
      articles,
      settings: data.settings || {},
    };
    return _cache;
  } catch (e) {
    // Fallback to local static content
    const mod = await import("./articles.js");
    _cache = { sections: mod.SECTIONS || [], articles: mod.ARTICLES || [], settings: {} };
    return _cache;
  }
}
