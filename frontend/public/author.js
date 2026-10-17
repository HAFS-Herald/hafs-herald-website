import { loadContent } from "./content.js";
import { $, articleCard, initPage, setTopbar, showLoadError, sortArticles, splitAuthors } from "./common.js";

const grid = $("#grid");

async function main() {
  initPage();

  const name = (new URLSearchParams(location.search).get("name") || "").trim();
  $("#authorName").textContent = name || "Author";
  $("#authorInitial").textContent = (name || "?").slice(0, 1).toUpperCase();
  document.title = `${name || "Author"} • The HAFS Herald`;

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

  const key = name.toLowerCase();
  const mine = sortArticles(
    articles.filter((a) => splitAuthors(a.author).some((n) => n.toLowerCase() === key)),
    "newest"
  );

  // Role from the staff list in Settings, if they're on it
  const staff = (settings.staff || []).find((p) => (p.name || "").trim().toLowerCase() === key);
  const sections = [...new Set(mine.map((a) => a.section))];
  const parts = [];
  if (staff?.role) parts.push(staff.role);
  if (mine.length) parts.push(`${mine.length} article${mine.length === 1 ? "" : "s"}`);
  if (sections.length) parts.push(sections.join(", "));
  $("#authorMeta").textContent = parts.join(" · ");

  grid.replaceChildren(...mine.map(articleCard));
  $("#empty").hidden = mine.length !== 0;
}

main();
