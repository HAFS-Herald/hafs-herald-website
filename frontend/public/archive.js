import { loadContent } from "./content.js";

const $ = (sel) => document.querySelector(sel);

const searchEl = $("#archiveSearch");
const sectionEl = $("#archiveSection");
const yearEl = $("#archiveYear");
const sortEl = $("#archiveSort");

const groupsRoot = $("#archiveGroups");
const emptyEl = $("#archiveEmpty");
const summaryEl = $("#archiveSummary");

const themeBtn = $("#themeBtn");
const yearSpan = $("#yearSpan");

const todayPill = $("#todayPill");
const issuePill = $("#issuePill");
const quoteEl = document.getElementById("quoteArchive");

function pickRandom(list) {
  const arr = (list || []).filter(Boolean);
  if (!arr.length) return "";
  return arr[Math.floor(Math.random() * arr.length)];
}

const ARCHIVE_QUOTES = [
  "Archives are where arguments go to become evidence.",
  "Yesterday’s news is tomorrow’s context.",
  "The past doesn’t repeat; it accumulates.",
  "Search isn’t nostalgia—it's verification.",
];

function setArchiveQuoteOnce() {
  if (!quoteEl) return;
  quoteEl.textContent = pickRandom(ARCHIVE_QUOTES);
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

function normalize(s) {
  return (s || "").toLowerCase().trim();
}

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function monthKey(iso) {
  const d = new Date(iso + "T00:00:00");
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${y}-${m}`;
}

function monthLabel(key) {
  const [y, m] = key.split("-").map(Number);
  const d = new Date(`${y}-${String(m).padStart(2, "0")}-01T00:00:00`);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "long" });
}

function uniqueYears(articles) {
  const years = new Set(articles.map(a => new Date(a.date + "T00:00:00").getFullYear()));
  return Array.from(years).sort((a,b) => b - a);
}

function buildFilters(sections, articles) {
  for (const s of sections) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    sectionEl.appendChild(opt);
  }

  for (const y of uniqueYears(articles)) {
    const opt = document.createElement("option");
    opt.value = String(y);
    opt.textContent = String(y);
    yearEl.appendChild(opt);
  }
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

function filterArticles(list) {
  const q = normalize(searchEl.value);
  const sec = sectionEl.value;
  const yr = yearEl.value;

  return list.filter(a => {
    const hay = normalize(`${a.title} ${a.summary} ${a.author} ${a.section}`);
    const matchQ = q ? hay.includes(q) : true;
    const matchSec = (sec === "all") ? true : a.section === sec;
    const sectionMatch = matchSec;
    const matchYr = (yr === "all") ? true : (new Date(a.date + "T00:00:00").getFullYear() === Number(yr));
    return matchQ && sectionMatch && matchYr;
  });
}

function groupByMonth(list) {
  const map = new Map();
  for (const a of list) {
    const key = monthKey(a.date);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(a);
  }
  const keys = Array.from(map.keys()).sort((a,b) => (a < b ? 1 : -1));
  return keys.map(k => ({ key: k, label: monthLabel(k), items: map.get(k) }));
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


async function main() {
  yearSpan.textContent = String(new Date().getFullYear());
  initTheme();

  const { sections, articles, settings } = await loadContent();

  // Topbar pills
  const now = new Date();
  if (todayPill) {
    todayPill.textContent = now.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  }
  if (issuePill) {
    issuePill.textContent = `Issue #${settings.currentIssueNumber ?? "—"}`;
  }

  buildFilters(sections, articles);

  function render() {
    const filtered = filterArticles(articles);
    const sorted = sortArticles(filtered, sortEl.value);
    const groups = groupByMonth(sorted);

    summaryEl.textContent = `${sorted.length} article${sorted.length === 1 ? "" : "s"} shown.`;
    groupsRoot.innerHTML = "";

    for (const g of groups) {
      const section = document.createElement("section");
      section.className = "archive-group card";
      section.innerHTML = `
        <div class="archive-group-head">
          <h2 class="headline-md" style="margin:0;">${escapeHtml(g.label)}</h2>
          <div class="muted">${g.items.length} item${g.items.length === 1 ? "" : "s"}</div>
        </div>
        <div class="archive-list">
          ${g.items.map(a => `
            <a class="archive-item" href="article.html?id=${encodeURIComponent(a.id)}">
              <div class="archive-item-top">
                <span class="tag">${escapeHtml(a.section)}</span>
                <span class="muted small">• ${formatDate(a.date)}</span>
              </div>
              <div class="archive-item-title"><strong>${escapeHtml(a.title)}</strong></div>
              <div class="muted small">By ${escapeHtml(a.author)} — ${escapeHtml(a.summary || "")}</div>
            </a>
          `).join("")}
        </div>
      `;
      groupsRoot.appendChild(section);
    }

    emptyEl.hidden = sorted.length !== 0;
  }

  render();

  searchEl.addEventListener("input", render);
  sectionEl.addEventListener("change", render);
  yearEl.addEventListener("change", render);
  sortEl.addEventListener("change", render);
  setArchiveQuoteOnce();
}

main();
