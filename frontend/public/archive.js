import { loadContent } from "./content.js";
import { $, cleanAuthor, escapeHtml, formatDate, initPage, listIssues, normalize, setTopbar, showLoadError, sortArticles, uniqueYears, yearOf } from "./common.js";

const searchEl = $("#archiveSearch");
const sectionEl = $("#archiveSection");
const yearEl = $("#archiveYear");
const sortEl = $("#archiveSort");

const groupsRoot = $("#archiveGroups");
const emptyEl = $("#archiveEmpty");
const summaryEl = $("#archiveSummary");

function monthKey(iso) {
  const d = new Date(iso + "T00:00:00");
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(key) {
  return new Date(`${key}-01T00:00:00`).toLocaleDateString(undefined, { year: "numeric", month: "long" });
}

function groupByMonth(list) {
  const map = new Map();
  for (const a of list) {
    const key = monthKey(a.date);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(a);
  }
  return Array.from(map.keys())
    .sort((a, b) => (a < b ? 1 : -1))
    .map((k) => ({ label: monthLabel(k), items: map.get(k) }));
}

async function main() {
  initPage();

  let content;
  try {
    content = await loadContent();
  } catch (err) {
    console.error(err);
    showLoadError(groupsRoot);
    return;
  }
  const { sections, articles, settings } = content;
  setTopbar(settings);

  $("#issues").innerHTML = listIssues(articles, settings)
    .map(
      (i) => `
      <a class="issue-chip" href="issue.html?n=${i.n}">
        <strong>Issue #${i.n}</strong>
        <span class="muted small">${formatDate(i.date)} · ${i.items.length} article${i.items.length === 1 ? "" : "s"}</span>
      </a>`
    )
    .join("");

  for (const s of sections) sectionEl.add(new Option(s, s));
  for (const y of uniqueYears(articles)) yearEl.add(new Option(String(y), String(y)));

  function render() {
    const q = normalize(searchEl.value);
    const filtered = articles.filter((a) => {
      if (q && !normalize(`${a.title} ${a.summary} ${a.author} ${a.section}`).includes(q)) return false;
      if (sectionEl.value !== "all" && a.section !== sectionEl.value) return false;
      if (yearEl.value !== "all" && yearOf(a.date) !== Number(yearEl.value)) return false;
      return true;
    });
    const sorted = sortArticles(filtered, sortEl.value);

    summaryEl.textContent = `${sorted.length} article${sorted.length === 1 ? "" : "s"} shown.`;
    groupsRoot.innerHTML = groupByMonth(sorted)
      .map(
        (g) => `
        <section class="archive-group card">
          <div class="archive-group-head">
            <h2 class="headline-md" style="margin:0;">${escapeHtml(g.label)}</h2>
            <div class="muted">${g.items.length} item${g.items.length === 1 ? "" : "s"}</div>
          </div>
          <div class="archive-list">
            ${g.items
              .map(
                (a) => `
              <a class="archive-item" href="article.html?id=${encodeURIComponent(a.id)}">
                <div class="archive-item-top">
                  <span class="tag">${escapeHtml(a.section)}</span>
                  <span class="muted small">• ${formatDate(a.date)}</span>
                </div>
                <div class="archive-item-title"><strong>${escapeHtml(a.title)}</strong></div>
                <div class="muted small">By ${escapeHtml(cleanAuthor(a.author))} — ${escapeHtml(a.summary || "")}</div>
              </a>`
              )
              .join("")}
          </div>
        </section>`
      )
      .join("");
    emptyEl.hidden = sorted.length !== 0;
  }

  render();
  searchEl.addEventListener("input", render);
  sectionEl.addEventListener("change", render);
  yearEl.addEventListener("change", render);
  sortEl.addEventListener("change", render);
}

main();
