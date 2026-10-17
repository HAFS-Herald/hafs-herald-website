import { loadContent } from "./content.js";
import { $, escapeHtml, initPage, setTopbar, showLoadError } from "./common.js";

const grid = $("#staffGrid");
const empty = $("#staffEmpty");

async function main() {
  initPage();

  let content;
  try {
    content = await loadContent();
  } catch (err) {
    console.error(err);
    showLoadError(grid);
    return;
  }
  const { settings } = content;
  setTopbar(settings);

  const staff = settings.staff || [];
  grid.replaceChildren(
    ...staff.map((person) => {
      const card = document.createElement("div");
      card.className = "staff-card";
      const initial = (person.name || "?").trim().slice(0, 1).toUpperCase();
      card.innerHTML = `
        <div class="avatar" aria-hidden="true">${escapeHtml(initial)}</div>
        <div>
          <div class="staff-name">${escapeHtml(person.role || "")}</div>
          <div class="muted">${escapeHtml(person.name || "")}</div>
          ${person.contact ? `<div class="muted small">${escapeHtml(person.contact)}</div>` : ""}
        </div>
      `;
      return card;
    })
  );
  empty.hidden = staff.length !== 0;
}

main();
