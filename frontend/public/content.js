// Loads content from the backend CMS if available.
// If the backend isn't running, falls back to local ./articles.js.

let _cache = null;

export async function loadContent() {
  if (_cache) return _cache;

  // Try backend first
  try {
    const API = window.HAFS_API_BASE || "";
    const r = await fetch(`${API}/api/content`, { cache: "no-store" });
    if (!r.ok) throw new Error("Backend unavailable");
    const data = await r.json();
    _cache = { sections: data.sections || [], articles: data.articles || [], settings: data.settings || {} };
    return _cache;
  } catch (e) {
    // Fallback to local static content
    const mod = await import("./articles.js");
    _cache = { sections: mod.SECTIONS || [], articles: mod.ARTICLES || [], settings: {} };
    return _cache;
  }
}
