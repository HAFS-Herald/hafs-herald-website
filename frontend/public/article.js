import { loadContent } from "./content.js";

const $ = (sel) => document.querySelector(sel);

const articleRoot = $("#articleRoot");
const relatedList = $("#relatedList");
const yearSpan = $("#yearSpan");
const menuBtn = document.getElementById("menuBtn");
const siteNav = document.getElementById("siteNav");

const todayPill = document.getElementById("todayPill");
const issuePill = document.getElementById("issuePill");

const themeBtn = $("#themeBtn");
const printBtn = $("#printBtn");
const copyLinkBtn = $("#copyLinkBtn");
const mailShare = $("#mailShare");

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function readingTime(text) {
  const words = (text || "").trim().split(/\s+/).filter(Boolean).length;
  const minutes = Math.max(1, Math.round(words / 220));
  return `${minutes} min read`;
}

function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "dark") document.documentElement.dataset.theme = "dark";
  themeBtn?.addEventListener("click", () => {
    const isDark = document.documentElement.dataset.theme === "dark";
    document.documentElement.dataset.theme = isDark ? "" : "dark";
    localStorage.setItem("theme", isDark ? "light" : "dark");
  });
}

function getIdFromUrl() {
  const params = new URLSearchParams(location.search);
  return params.get("id");
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderArticle(a) {
  document.title = `${a.title} • The HAFS Herald`;

  const bodyText = (a.body || []).join(" ");
  const rt = readingTime(bodyText);

  const pull = a.pullQuote ? `
    <blockquote>
      <p style="margin:0;"><strong>${escapeHtml(a.pullQuote)}</strong></p>
    </blockquote>
  ` : "";

  const paragraphs = (a.body || []).map(p => renderBlock(p)).join("");

function renderBlock(p) {
  const s = String(p || "").trim();
  // Image block syntax (one paragraph):
  // ![alt text](path/to/image.jpg "Optional caption")
  const m = s.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]+)")?\)$/);
  if (m) {
    const alt = escapeHtml(m[1] || "");
    const src = escapeHtml(m[2] || "");
    const cap = escapeHtml(m[3] || "");
    return `
      <figure class="inline-media">
        <img src="${src}" alt="${alt}" loading="lazy" />
        ${cap ? `<figcaption class="muted small">${cap}</figcaption>` : ""}
      </figure>
    `;
  }
  return `<p>${escapeHtml(s)}</p>`;
}

  articleRoot.innerHTML = `
    <div class="kicker">
      <span class="tag tag-accent">${escapeHtml(a.section)}</span>
      <span>•</span>
      <span>${formatDate(a.date)}</span>
    </div>

    <h1>${escapeHtml(a.title)}</h1>

    <div class="article-meta">
      <span>By ${escapeHtml(a.author)}</span>
      <span>•</span>
      <span>${rt}</span>
    </div>

    ${a.cover ? `
      <div class="cover">
        <img src="${a.cover}" alt="" loading="lazy" />
      </div>
    ` : ""}

    <div class="prose">
      <p class="muted"><em>${escapeHtml(a.summary || "")}</em></p>
      ${pull}
      ${paragraphs}
    </div>
  `;

  const url = location.href;
  mailShare.href = `mailto:?subject=${encodeURIComponent(a.title)}&body=${encodeURIComponent(url)}`;
  copyLinkBtn?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(url);
      copyLinkBtn.textContent = "Copied!";
      setTimeout(() => (copyLinkBtn.textContent = "Copy link"), 1200);
    } catch {
      prompt("Copy this link:", url);
    }
  });
}

function renderRelated(all, current) {
  const related = all
    .filter(a => a.id !== current.id && a.section === current.section)
    .sort((a,b) => new Date(b.date) - new Date(a.date))
    .slice(0, 5);

  relatedList.innerHTML = related.length
    ? related.map(a => `
        <a class="related-item" href="article.html?id=${encodeURIComponent(a.id)}">
          <div class="muted small">${escapeHtml(a.section)} • ${formatDate(a.date)}</div>
          <div><strong>${escapeHtml(a.title)}</strong></div>
        </a>
      `).join("")
    : `<p class="muted">No related articles yet.</p>`;
}

async function main() {
  yearSpan.textContent = String(new Date().getFullYear());
  initTheme();

  printBtn?.addEventListener("click", (e) => {
    e.preventDefault();
    window.print();
  });

  const { articles, settings } = await loadContent();

  const now = new Date();
  if (todayPill) todayPill.textContent = now.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  if (issuePill) issuePill.textContent = `Issue #${settings.currentIssueNumber ?? "—"}`;

  if (menuBtn && siteNav) {
    menuBtn.addEventListener("click", () => siteNav.classList.toggle("is-open"));
  }
  const id = getIdFromUrl();
  const article = articles.find(a => a.id === id) || articles[0];

  if (!article) {
    articleRoot.innerHTML = `<p>Article not found.</p>`;
    return;
  }

  renderArticle(article);
  renderRelated(articles, article);
}

main();
