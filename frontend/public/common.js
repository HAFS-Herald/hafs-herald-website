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
      <span>By ${escapeHtml(a.author)}</span>
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
