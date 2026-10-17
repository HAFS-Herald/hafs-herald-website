import { loadContent } from "./content.js";
import { $, articleCard, initPage, normalize, setTopbar, showLoadError, sortArticles, uniqueYears, yearOf } from "./common.js";

const searchEl = $("#search");
const issueEl = $("#issueSelect");
const yearEl = $("#yearSelect");
const sortEl = $("#sort");
const grid = $("#grid");
const empty = $("#empty");

async function main() {
  initPage();

  const section = new URLSearchParams(location.search).get("section") || "News";
  document.title = `${section} • The HAFS Herald`;
  $("#sectionTitle").textContent = section;

  let content;
  try {
    content = await loadContent();
  } catch (err) {
    console.error(err);
    showLoadError(grid);
    return;
  }
  const { articles, settings } = content;
  setTopbar(settings);

  const sectionArticles = articles.filter((a) => a.section === section);
  for (const y of uniqueYears(sectionArticles)) yearEl.add(new Option(String(y), String(y)));

  function render() {
    const q = normalize(searchEl.value);
    let list = sectionArticles;
    if (issueEl.value === "current") list = list.filter((a) => Number(a.issue) === Number(settings.currentIssueNumber));
    if (yearEl.value !== "all") list = list.filter((a) => yearOf(a.date) === Number(yearEl.value));
    if (q) list = list.filter((a) => normalize(`${a.title} ${a.summary} ${a.author}`).includes(q));

    grid.replaceChildren(...sortArticles(list, sortEl.value).map(articleCard));
    empty.hidden = list.length !== 0;
  }

  render();
  searchEl.addEventListener("input", render);
  issueEl.addEventListener("change", render);
  yearEl.addEventListener("change", render);
  sortEl.addEventListener("change", render);
}

main();
