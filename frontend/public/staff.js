import { loadContent } from "./content.js";

const $ = (sel) => document.querySelector(sel);

const themeBtn = $("#themeBtn");
const menuBtn = $("#menuBtn");
const siteNav = $("#siteNav");

const todayPill = $("#todayPill");
const issuePill = $("#issuePill");
const yearSpan = $("#yearSpan");

const quoteEl = $("#staffQuote");

function pickRandom(list) {
  const arr = (list || []).filter(Boolean);
  if (!arr.length) return "";
  return arr[Math.floor(Math.random() * arr.length)];
}

const STAFF_QUOTES = [
  "The newsroom is a team sport with individual sentences.",
  "Editing is care made visible.",
  "A staff page is an ethics statement in disguise.",
  "We learn by writing: Discimus Scribendo.",
];

function setStaffQuoteOnce() {
  if (!quoteEl) return;
  quoteEl.textContent = pickRandom(STAFF_QUOTES);
}

const grid = $("#staffGrid");
const empty = $("#staffEmpty");

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


async function main() {
  initTheme();
  initMenu();

  const { settings } = await loadContent();

  const now = new Date();
  todayPill.textContent = now.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
  yearSpan.textContent = String(now.getFullYear());
  issuePill.textContent = `Issue #${settings.currentIssueNumber ?? "—"}`;

  const staff = settings.staff || [];
  grid.innerHTML = "";

  for (const person of staff) {
    const card = document.createElement("div");
    card.className = "staff-card";
    const initial = (person.name || "?").trim().slice(0,1).toUpperCase();
    card.innerHTML = `
      <div class="avatar" aria-hidden="true">${initial}</div>
      <div>
        <div class="staff-name">${person.role || ""}</div>
        <div class="muted">${person.name || ""}</div>
        ${person.contact ? `<div class="muted small">${person.contact}</div>` : ""}
      </div>
    `;
    grid.appendChild(card);
  }

  empty.hidden = staff.length !== 0;
  setStaffQuoteOnce();
}

main();
