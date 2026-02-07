import { loadContent } from "./content.js";

const $ = (sel) => document.querySelector(sel);

const themeBtn = $("#themeBtn");
const menuBtn = $("#menuBtn");
const siteNav = $("#siteNav");

const todayPill = $("#todayPill");
const issuePill = $("#issuePill");
const yearSpan = $("#yearSpan");

const titleEl = $("#sectionTitle");
const quoteEl = $("#sectionQuote");

function pickRandom(list) {
  const arr = (list || []).filter(Boolean);
  if (!arr.length) return "";
  return arr[Math.floor(Math.random() * arr.length)];
}

const SECTION_QUOTES = [
  "A section is a lens, not a box.",
  "Different topics; same standard: get it right.",
  "Curiosity is a discipline.",
  "Follow the data, then follow the consequences.",
];

function setSectionQuoteOnce() {
  if (!quoteEl) return;
  quoteEl.textContent = pickRandom(SECTION_QUOTES);
}


const searchEl = $("#search");
const issueEl = $("#issueSelect");
const yearEl = $("#yearSelect");
const sortEl = $("#sort");

const grid = $("#grid");
const empty = $("#empty");

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

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function normalize(s) {
  return (s || "").toLowerCase().trim();
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

function uniqueYears(list) {
  const years = new Set(list.map(a => new Date(a.date + "T00:00:00").getFullYear()));
  return Array.from(years).sort((a,b) => b - a);
}

function renderGrid(list) {
  grid.innerHTML = "";
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
        <span>${formatDate(a.date)}</span>
      </div>
      <h3><a class="title-link" href="article.html?id=${encodeURIComponent(a.id)}">${a.title}</a></h3>
      <p class="muted" style="margin:0;">${a.summary}</p>
      <div class="meta"><span>By ${a.author}</span></div>
    `;
    grid.appendChild(card);
  }
  empty.hidden = list.length !== 0;
}


async function main() {
  initTheme();
  initMenu();

  const { articles, settings } = await loadContent();

  const now = new Date();
  todayPill.textContent = now.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  yearSpan.textContent = String(now.getFullYear());

  const issueNum = settings.currentIssueNumber ?? "—";
  issuePill.textContent = `Issue #${issueNum}`;

  const params = new URLSearchParams(location.search);
  const section = params.get("section") || "News";
  document.title = `${section} • The HAFS Herald`;
  titleEl.textContent = section;

  const sectionArticles = articles.filter(a => a.section === section);

  // years dropdown
  for (const y of uniqueYears(sectionArticles)) {
    const opt = document.createElement("option");
    opt.value = String(y);
    opt.textContent = String(y);
    yearEl.appendChild(opt);
  }

  function rerender() {
    const q = normalize(searchEl.value);
    const issueMode = issueEl.value;
    const year = yearEl.value;

    let list = sectionArticles;

    if (issueMode === "current") {
      list = list.filter(a => Number(a.issue) === Number(settings.currentIssueNumber));
    }

    if (year !== "all") {
      list = list.filter(a => new Date(a.date + "T00:00:00").getFullYear() === Number(year));
    }

    if (q) {
      list = list.filter(a => {
        const hay = normalize(`${a.title} ${a.summary} ${a.author}`);
        return hay.includes(q);
      });
    }

    list = sortArticles(list, sortEl.value);
    renderGrid(list);
  }

  rerender();

  searchEl.addEventListener("input", rerender);
  issueEl.addEventListener("change", rerender);
  yearEl.addEventListener("change", rerender);
  sortEl.addEventListener("change", rerender);
  setSectionQuoteOnce();
}

main();
