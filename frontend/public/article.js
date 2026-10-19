import { loadContent } from "./content.js";
import { $, authorLinks, escapeHtml, formatDate, formatInline, initPage, readingTime, setTopbar, showLoadError } from "./common.js";

const articleRoot = $("#articleRoot");
const relatedList = $("#relatedList");
const copyLinkBtn = $("#copyLinkBtn");

function renderBlock(p) {
  const s = String(p || "").trim();
  // Image block syntax (one paragraph):
  // ![alt text](path/to/image.jpg "Optional caption")
  const m = s.match(/^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]+)")?\)$/);
  if (m) {
    const cap = escapeHtml(m[3] || "");
    return `
      <figure class="inline-media">
        <img src="${escapeHtml(m[2] || "")}" alt="${escapeHtml(m[1] || "")}" loading="lazy" />
        ${cap ? `<figcaption class="muted small">${cap}</figcaption>` : ""}
      </figure>
    `;
  }
  if (s.startsWith("## ")) return `<h2>${formatInline(s.slice(3))}</h2>`;
  const lines = s.split("\n");
  if (lines.every((ln) => /^[-*] /.test(ln))) {
    return `<ul>${lines.map((ln) => `<li>${formatInline(ln.slice(2))}</li>`).join("")}</ul>`;
  }
  // "1. First" / "2) Second" lines; mirrors render_body_block() in backend/app.py
  const numbered = /^(\d{1,3})[.)] /;
  if (lines.every((ln) => numbered.test(ln))) {
    const first = Number(lines[0].match(numbered)[1]);
    const items = lines.map((ln) => `<li>${formatInline(ln.replace(numbered, ""))}</li>`).join("");
    return `<ol${first !== 1 ? ` start="${first}"` : ""}>${items}</ol>`;
  }
  return `<p>${formatInline(s)}</p>`;
}

function renderArticle(a) {
  document.title = `${a.title} • The HAFS Herald`;

  const pull = a.pullQuote
    ? `<blockquote><p style="margin:0;"><strong>${escapeHtml(a.pullQuote)}</strong></p></blockquote>`
    : "";

  articleRoot.innerHTML = `
    <div class="kicker">
      <span class="tag tag-accent">${escapeHtml(a.section)}</span>
      <span>•</span>
      <span>${formatDate(a.date)}</span>
    </div>

    <h1>${escapeHtml(a.title)}</h1>

    <div class="article-meta">
      <span>By ${authorLinks(a.author)}</span>
      <span>•</span>
      <span>${readingTime(a.body)}</span>
    </div>

    ${a.cover ? `<div class="cover"><img src="${escapeHtml(a.cover)}" alt="" loading="lazy" /></div>` : ""}

    <div class="prose">
      ${a.correction ? `<div class="correction"><strong>Correction:</strong> ${escapeHtml(a.correction)}</div>` : ""}
      <p class="muted"><em>${escapeHtml(a.summary || "")}</em></p>
      ${pull}
      ${(a.body || []).map(renderBlock).join("")}
    </div>
  `;

  const url = location.href;
  $("#mailShare").href = `mailto:?subject=${encodeURIComponent(a.title)}&body=${encodeURIComponent(url)}`;
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
    .filter((a) => a.id !== current.id && a.section === current.section)
    .sort((a, b) => new Date(b.date) - new Date(a.date))
    .slice(0, 5);

  relatedList.innerHTML = related.length
    ? related
        .map(
          (a) => `
        <a class="related-item" href="article.html?id=${encodeURIComponent(a.id)}">
          <div class="muted small">${escapeHtml(a.section)} • ${formatDate(a.date)}</div>
          <div><strong>${escapeHtml(a.title)}</strong></div>
        </a>`
        )
        .join("")
    : `<p class="muted">No related articles yet.</p>`;
}

async function main() {
  initPage();
  $("#printBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    window.print();
  });

  let content;
  try {
    content = await loadContent();
  } catch (err) {
    console.error(err);
    showLoadError(articleRoot);
    return;
  }
  const { articles, settings } = content;
  setTopbar(settings);

  const id = new URLSearchParams(location.search).get("id");
  const article = articles.find((a) => a.id === id);
  if (!article) {
    articleRoot.innerHTML = `<h1>Article not found</h1><p class="muted">It may have been moved or unpublished. <a href="archive.html">Browse the archive</a>.</p>`;
    relatedList.innerHTML = "";
    return;
  }

  renderArticle(article);
  renderRelated(articles, article);
}

main();
