// Shared helpers used by every page: header controls, formatting, article cards.

export const $ = (sel) => document.querySelector(sel);

export function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

// **bold**, *italic* and [links](https://...). Escapes first, so it's safe on any text.
// Mirrors format_inline() in backend/app.py.
export function formatInline(text) {
  return escapeHtml(text)
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/|mailto:|\/)[^\s)]*)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])/g, "<em>$1</em>");
}

export function normalize(s) {
  return (s || "").toLowerCase().trim();
}

function parseDate(iso) {
  return new Date(iso + "T00:00:00");
}

export function formatDate(iso, month = "short") {
  if (!iso) return "—";
  return parseDate(iso).toLocaleDateString(undefined, { year: "numeric", month, day: "numeric" });
}

export function yearOf(iso) {
  return parseDate(iso).getFullYear();
}

export function readingTime(body) {
  const words = (body || []).join(" ").trim().split(/\s+/).filter(Boolean).length;
  return `${Math.max(1, Math.round(words / 220))} min read`;
}

export function sortArticles(list, mode) {
  const copy = [...list];
  if (mode === "title") return copy.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
  copy.sort((a, b) => new Date(a.date) - new Date(b.date));
  if (mode === "newest") copy.reverse();
  return copy;
}

export function uniqueYears(articles) {
  return Array.from(new Set(articles.map((a) => yearOf(a.date)))).sort((a, b) => b - a);
}

// Every issue that has published articles, newest first.
// The current issue uses its date from Settings; older ones use their latest article's date.
export function listIssues(articles, settings) {
  const byIssue = new Map();
  for (const a of articles) {
    const n = Number(a.issue);
    if (!n) continue;
    if (!byIssue.has(n)) byIssue.set(n, []);
    byIssue.get(n).push(a);
  }
  return Array.from(byIssue.entries())
    .map(([n, items]) => ({
      n,
      items,
      date: n === Number(settings.currentIssueNumber) && settings.currentIssueDate
        ? settings.currentIssueDate
        : items.map((a) => a.date).sort().at(-1),
    }))
    .sort((a, b) => b.n - a.n);
}

// "Kim Ji-woo and Lee Min" / "Kim, Lee & Park" -> individual names
export function splitAuthors(author) {
  return String(author || "")
    .split(/\s*(?:,|&|\band\b|·)\s*/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function authorUrl(name) {
  return `author.html?name=${encodeURIComponent(name)}`;
}

// Byline with each name linking to its author page
export function authorLinks(author) {
  const names = splitAuthors(author);
  if (!names.length) return escapeHtml(author || "");
  const links = names.map((n) => `<a class="byline-link" href="${authorUrl(n)}">${escapeHtml(n)}</a>`);
  return links.length > 1 ? `${links.slice(0, -1).join(", ")} and ${links.at(-1)}` : links[0];
}

// The issue's lead: the one editors marked, otherwise the newest article with a photo
export function pickLead(articles) {
  return articles.find((a) => a.lead) || sortArticles(articles.filter((a) => a.cover), "newest")[0] || null;
}

export function leadStory(a) {
  const el = document.createElement("article");
  el.className = "card lead-story";
  const href = `article.html?id=${encodeURIComponent(a.id)}`;
  el.innerHTML = `
    ${a.cover ? `<a class="lead-media" href="${href}" tabindex="-1" aria-hidden="true"><img src="${escapeHtml(a.cover)}" alt=""></a>` : ""}
    <div class="lead-body">
      <div class="kicker">
        <span class="tag tag-accent">${escapeHtml(a.section)}</span>
        <span>•</span>
        <span>${formatDate(a.date)}</span>
      </div>
      <h2 class="lead-title"><a class="title-link" href="${href}">${escapeHtml(a.title)}</a></h2>
      <p class="lede">${escapeHtml(a.summary)}</p>
      <div class="meta">
        <span>By ${authorLinks(a.author)}</span>
        <span>•</span>
        <span>${readingTime(a.body)}</span>
      </div>
    </div>
  `;
  return el;
}

export function articleCard(a) {
  const card = document.createElement("div");
  card.className = "card article-card";
  card.innerHTML = `
    <div class="thumb">
      ${a.cover ? `<img src="${escapeHtml(a.cover)}" alt="" loading="lazy">` : ""}
    </div>
    <div class="kicker">
      <span class="tag">${escapeHtml(a.section)}</span>
      <span>•</span>
      <span>${formatDate(a.date)}</span>
    </div>
    <h3>
      <a class="title-link" href="article.html?id=${encodeURIComponent(a.id)}">${escapeHtml(a.title)}</a>
    </h3>
    <p class="muted" style="margin:0;">${escapeHtml(a.summary)}</p>
    <div class="meta">
      <span>By ${authorLinks(a.author)}</span>
      <span>•</span>
      <span>${readingTime(a.body)}</span>
    </div>
  `;
  return card;
}

// Theme toggle, mobile menu and footer year. Runs before content loads.
export function initPage() {
  const themeBtn = $("#themeBtn");
  const menuBtn = $("#menuBtn");
  const siteNav = $("#siteNav");

  try {
    if (localStorage.getItem("theme") === "dark") document.documentElement.dataset.theme = "dark";
  } catch (_) {}
  themeBtn?.addEventListener("click", () => {
    const isDark = document.documentElement.dataset.theme === "dark";
    document.documentElement.dataset.theme = isDark ? "" : "dark";
    try { localStorage.setItem("theme", isDark ? "light" : "dark"); } catch (_) {}
  });

  menuBtn?.addEventListener("click", () => siteNav?.classList.toggle("is-open"));

  const yearSpan = $("#yearSpan");
  if (yearSpan) yearSpan.textContent = String(new Date().getFullYear());
}

// Date and issue pills in the header.
export function setTopbar(settings) {
  const todayPill = $("#todayPill");
  const issuePill = $("#issuePill");
  if (todayPill) {
    todayPill.textContent = new Date().toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  }
  if (issuePill) issuePill.textContent = `Issue #${settings.currentIssueNumber ?? "—"}`;
}

// Shown in place of content when the backend can't be reached.
export function showLoadError(container) {
  if (!container) return;
  container.innerHTML = `<p class="muted">Articles couldn’t be loaded right now. Please refresh in a moment.</p>`;
}
