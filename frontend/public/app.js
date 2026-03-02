import { loadContent } from "./content.js";

const $ = (sel) => document.querySelector(sel);
const API = window.HAFS_API_BASE || "";

const articlesGrid = $("#articlesGrid");
const featuredCard = $("#featuredCard");
const searchInput = $("#searchInput");
const sortSelect = $("#sortSelect");
const emptyState = $("#emptyState");

const themeBtn = $("#themeBtn");
const menuBtn = $("#menuBtn");
const siteNav = $("#siteNav");

const todayPill = $("#todayPill");
const issuePill = $("#issuePill");
const yearSpan = $("#yearSpan");

const issueHeadline = $("#issueHeadline");
const issueDateEl = $("#issueDate");

const quoteLatest = $("#quoteLatest");
const quoteSections = $("#quoteSections");
const quoteStaff = $("#quoteStaff");
const quoteNewsletter = $("#quoteNewsletter");
const quoteContact = $("#quoteContact");

const tipLink = $("#tipLink");
const tipModal = $("#tipModal");

const newsletterForm = $("#newsletterForm");
const newsletterHint = $("#newsletterHint");

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

function shortDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function readingTime(text) {
  const words = (text || "").trim().split(/\s+/).filter(Boolean).length;
  const minutes = Math.max(1, Math.round(words / 220));
  return `${minutes} min read`;
}

function normalize(s) {
  return (s || "").toLowerCase().trim();
}

function pickRandom(list) {
  const arr = (list || []).filter(Boolean);
  if (!arr.length) return "";
  return arr[Math.floor(Math.random() * arr.length)];
}

// Quotes are unique per section and only change when the page refreshes.
const QUOTES = {
  latest: [
    "If it’s worth knowing, it’s worth verifying.",
    "The first draft lies; the second draft argues.",
    "Small campus, big consequences.",
    "We report; you decide what it means.",
  ],
  sections: [
    "A map of attention, not an itinerary of certainty.",
    "Every section is a different way of being careful.",
    "The world is plural; the newsroom should be too.",
    "Turn the page—change the question.",
  ],
  staff: [
    "Bylines are promises with names attached.",
    "Editors are just writers with responsibility.",
    "A newsroom is a machine for humility.",
    "We disagree in draft so we can agree in print.",
  ],
  newsletter: [
    "Good writing travels light: one email, one idea.",
    "No spam. Just the week’s sharp edges.",
    "A small inbox is a moral achievement.",
    "Subscribe to the habit of paying attention.",
  ],
  contact: [
    "Tip us off. Argue with us. Correct us.",
    "The fastest way to improve a paper is a reader who cares.",
    "If we missed it, tell us.",
    "Conversation is a kind of accountability.",
  ],
};

function setQuotesOnce() {
  const map = [
    ["quoteLatest", QUOTES.latest],
    ["quoteSections", QUOTES.sections],
    ["quoteStaff", QUOTES.staff],
    ["quoteNewsletter", QUOTES.newsletter],
    ["quoteContact", QUOTES.contact],
  ];
  for (const [id, list] of map) {
    const el = document.getElementById(id);
    if (el) el.textContent = pickRandom(list);
  }
}


function setTopbar(settings) {
  const now = new Date();
  if (todayPill) todayPill.textContent = now.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  if (yearSpan) yearSpan.textContent = String(now.getFullYear());

  const n = settings.currentIssueNumber ?? "—";
  if (issuePill) issuePill.textContent = `Issue #${n}`;
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

function initMenu() {
  if (!menuBtn || !siteNav) return;
  menuBtn.addEventListener("click", () => siteNav.classList.toggle("is-open"));
}

function initTipsModal() {
  if (!tipLink || !tipModal) return;
  tipLink.addEventListener("click", (e) => {
    e.preventDefault();
    tipModal.showModal();
  });

  const closeBtn = document.getElementById("tipClose");
  const cancelBtn = document.getElementById("tipCancel");
  const doClose = () => { try { tipModal.close(); } catch(_) {} };
  closeBtn?.addEventListener("click", doClose);
  cancelBtn?.addEventListener("click", doClose);
}

function initTipsSubmit() {
  const tipForm = document.getElementById("tipForm");
  const tipModal = document.getElementById("tipModal");
  const tipMessage = document.getElementById("tipMessage");
  const tipHint = document.getElementById("tipHint");
  if (!tipForm || !tipModal) return;

  tipForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = (tipMessage?.value || "").trim();
    if (!message) return;

    try {
      const res = await fetch(`${API}/api/tip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Tip failed");

      if (tipHint) tipHint.textContent = "Tip sent. Thank you.";
      if (tipMessage) tipMessage.value = "";
      // close after short delay
      setTimeout(() => { try { tipModal.close(); } catch(_) {} }, 600);
    } catch (err) {
      console.error(err);
      if (tipHint) tipHint.textContent = "Couldn’t send right now. Try again.";
    }
  });
}

function initNewsletter() {
  if (!newsletterForm) return;
  const emailInput = newsletterForm.querySelector('input[type="email"]');
  newsletterForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = (emailInput?.value || "").trim();
    if (!email) return;

    try {
      const res = await fetch(`${API}/api/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Subscribe failed");

      if (newsletterHint) newsletterHint.textContent = "Subscribed. See you next issue.";
      if (emailInput) emailInput.value = "";
    } catch (err) {
      console.error(err);
      if (newsletterHint) newsletterHint.textContent = "Couldn’t subscribe right now. Try again.";
    }
  });
}

function sortArticles(list, mode) {
  const copy = [...list];
  if (mode === "title") {
    copy.sort((a,b) => (a.title || "").localeCompare(b.title || ""));
    return copy;
  }
  copy.sort((a,b) => new Date(a.date) - new Date(b.date));
  if (mode === "newest") copy.reverse();
  return copy;
}

function renderFeatured(list) {
  if (!featuredCard) return;
  const featured = list.find(a => a.featured) || list[0];
  if (!featured) {
    featuredCard.innerHTML = `<div class="body"><p class="muted">No articles yet.</p></div>`;
    return;
  }

  featuredCard.innerHTML = `
    <div class="media">
      ${featured.cover ? `<img src="${featured.cover}" alt="" loading="lazy">` : ""}
    </div>
    <div class="body">
      <div class="kicker">
        <span class="tag tag-accent">${featured.section}</span>
        <span>•</span>
        <span>${shortDate(featured.date)}</span>
      </div>
      <h2 class="headline-md" style="margin:0 0 .4rem 0;">
        <a class="title-link" href="article.html?id=${encodeURIComponent(featured.id)}">${featured.title}</a>
      </h2>
      <p class="muted" style="margin:.25rem 0 .75rem 0;">${featured.summary}</p>
      <div class="meta">
        <span>By ${featured.author}</span>
        <span>•</span>
        <span>${readingTime((featured.body || []).join(" "))}</span>
      </div>
    </div>
  `;
}

function renderGrid(list) {
  if (!articlesGrid) return;
  articlesGrid.innerHTML = "";

  for (const a of list) {
    const card = document.createElement("div");
    card.className = "card article-card";
    card.innerHTML = `
      <div class="thumb">
        ${a.cover ? `<img src="${a.cover}" alt="" loading="lazy">` : ""}
      </div>
      <div class="kicker">
        <span class="tag">${a.section}</span>
        <span>•</span>
        <span>${shortDate(a.date)}</span>
      </div>
      <h3>
        <a class="title-link" href="article.html?id=${encodeURIComponent(a.id)}">${a.title}</a>
      </h3>
      <p class="muted" style="margin:0;">${a.summary}</p>
      <div class="meta">
        <span>By ${a.author}</span>
        <span>•</span>
        <span>${readingTime((a.body || []).join(" "))}</span>
      </div>
    `;
    articlesGrid.appendChild(card);
  }

  emptyState.hidden = list.length !== 0;
}


async function main() {
  initTheme();
  initMenu();
  initTipsModal();
  initTipsSubmit();
  initNewsletter();

  const { articles, settings } = await loadContent();

  setTopbar(settings);

  const issueNum = settings.currentIssueNumber ?? null;
  const issueDate = settings.currentIssueDate ?? null;

  if (issueHeadline) issueHeadline.textContent = `Issue #${issueNum ?? "—"}`;
  if (issueDateEl) issueDateEl.textContent = formatDate(issueDate);

let issueArticles = (issueNum == null)
  ? [...articles]
  : articles.filter(a => Number(a.issue) === Number(issueNum));

// (optional but recommended) if issue filter returns nothing, show everything
if (issueNum != null && issueArticles.length === 0 && articles.length) {
  issueArticles = [...articles];
}

function rerender() {
  const q = normalize(searchInput?.value || "");
  const mode = sortSelect?.value || "newest";

  const filtered = issueArticles.filter(a => {
    const hay = normalize(`${a.title} ${a.summary} ${a.author} ${a.section}`);
    return q ? hay.includes(q) : true;
  });

  renderGrid(sortArticles(filtered, mode));
}

  rerender();

  searchInput?.addEventListener("input", rerender);
  sortSelect?.addEventListener("change", rerender);
    setQuotesOnce();
}

main();
