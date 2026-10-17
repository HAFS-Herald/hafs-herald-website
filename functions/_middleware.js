// Cloudflare Pages function: adds link-preview tags (Open Graph) to pages.
//
// Article content is loaded by JavaScript in the browser, but KakaoTalk,
// Instagram, Discord and search engines only read the raw HTML. This fills in
// the headline, summary and cover photo before the page is sent.
// Runs only on the paths listed in frontend/public/_routes.json.

const DEFAULT_API = "https://hafs-herald-website-production.up.railway.app";
const SITE_NAME = "The HAFS Herald";
const SITE_DESCRIPTION = "The HAFS Herald — student-run school newspaper.";

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function fetchArticle(api, id) {
  try {
    const r = await fetch(`${api}/api/article/${encodeURIComponent(id)}`, {
      cf: { cacheTtl: 300, cacheEverything: true },
    });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

export async function onRequest(context) {
  const response = await context.next();
  if (!(response.headers.get("content-type") || "").includes("text/html")) return response;

  const url = new URL(context.request.url);
  const api = (context.env.HAFS_API_BASE || DEFAULT_API).replace(/\/+$/, "");
  const id = /^\/article(\.html)?$/.test(url.pathname) ? url.searchParams.get("id") : null;
  const a = id ? await fetchArticle(api, id) : null;

  let image = `${url.origin}/assets/logo.png`;
  if (a?.cover) image = /^https?:\/\//.test(a.cover) ? a.cover : `${api}/${a.cover.replace(/^\/+/, "")}`;

  const description = a ? a.summary : SITE_DESCRIPTION;
  const tags = [
    ["property", "og:site_name", SITE_NAME],
    ["property", "og:type", a ? "article" : "website"],
    ["property", "og:title", a ? a.title : SITE_NAME],
    ["property", "og:description", description],
    ["property", "og:image", image],
    ["property", "og:url", url.href],
    ["name", "twitter:card", a?.cover ? "summary_large_image" : "summary"],
    ["name", "description", description],
  ];
  if (a) {
    tags.push(["property", "article:published_time", a.date], ["property", "article:section", a.section]);
  }
  const html = tags.map(([attr, key, value]) => `<meta ${attr}="${key}" content="${esc(value)}" />`).join("\n  ");

  let rewriter = new HTMLRewriter()
    .on('meta[name="description"]', { element: (el) => el.remove() })
    .on("head", { element: (el) => el.append(`  ${html}\n`, { html: true }) });
  if (a) rewriter = rewriter.on("title", { element: (el) => el.setInnerContent(`${a.title} • ${SITE_NAME}`) });
  return rewriter.transform(response);
}
