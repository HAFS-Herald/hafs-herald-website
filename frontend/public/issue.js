import { loadContent } from "./content.js";
import { $, articleCard, escapeHtml, formatDate, initPage, leadStory, listIssues, pickLead, setTopbar, showLoadError, sortArticles } from "./common.js";

const root = $("#issueSections");

async function main() {
  initPage();

  const n = Number(new URLSearchParams(location.search).get("n"));
  $("#issueTitle").textContent = `Issue #${n || "—"}`;
  document.title = `Issue #${n || "—"} • The HAFS Herald`;

  let content;
  try {
    content = await loadContent();
  } catch (err) {
    console.error(err);
    showLoadError(root);
    return;
  }
  const { sections, articles, settings } = content;
  setTopbar(settings);

  const issue = listIssues(articles, settings).find((i) => i.n === n);
  $("#empty").hidden = Boolean(issue);
  if (!issue) return;

  $("#issueMeta").textContent = `Published ${formatDate(issue.date, "long")} · ${issue.items.length} article${issue.items.length === 1 ? "" : "s"}`;

  const lead = pickLead(issue.items);
  if (lead) root.appendChild(leadStory(lead));

  // Like a table of contents: sections in their usual order
  const order = [...sections, ...new Set(issue.items.map((a) => a.section))];
  for (const section of new Set(order)) {
    const items = issue.items.filter((a) => a.section === section && a !== lead);
    if (!items.length) continue;
    const group = document.createElement("section");
    group.className = "issue-group";
    group.innerHTML = `<h2 class="headline-md">${escapeHtml(section)}</h2><div class="grid"></div>`;
    group.querySelector(".grid").replaceChildren(...sortArticles(items, "newest").map(articleCard));
    root.appendChild(group);
  }
}

main();
