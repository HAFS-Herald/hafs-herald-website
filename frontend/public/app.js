import { loadContent } from "./content.js";
import { $, articleCard, formatDate, initPage, leadStory, normalize, pickLead, setTopbar, showLoadError, sortArticles } from "./common.js";

const API = window.HAFS_API_BASE || "";

const articlesGrid = $("#articlesGrid");
const searchInput = $("#searchInput");
const sortSelect = $("#sortSelect");
const emptyState = $("#emptyState");

function initTipsModal() {
  const tipLink = $("#tipLink");
  const tipModal = $("#tipModal");
  if (!tipLink || !tipModal) return;

  tipLink.addEventListener("click", (e) => {
    e.preventDefault();
    tipModal.showModal();
  });
  const doClose = () => { try { tipModal.close(); } catch (_) {} };
  $("#tipClose")?.addEventListener("click", doClose);
  $("#tipCancel")?.addEventListener("click", doClose);

  const tipForm = $("#tipForm");
  const tipMessage = $("#tipMessage");
  const tipHint = $("#tipHint");
  tipForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = (tipMessage?.value || "").trim();
    if (!message) return;
    const website = tipForm.querySelector('input[name="website"]')?.value || "";

    try {
      const res = await fetch(`${API}/api/tip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, website }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "");

      if (tipHint) tipHint.textContent = "Tip sent. Thank you.";
      if (tipMessage) tipMessage.value = "";
      setTimeout(doClose, 600);
    } catch (err) {
      console.error(err);
      if (tipHint) tipHint.textContent = err.message || "Couldn’t send right now. Try again.";
    }
  });
}

function initNewsletter() {
  const form = $("#newsletterForm");
  const hint = $("#newsletterHint");
  if (!form) return;
  const emailInput = form.querySelector('input[type="email"]');

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = (emailInput?.value || "").trim();
    if (!email) return;
    const website = form.querySelector('input[name="website"]')?.value || "";

    try {
      const res = await fetch(`${API}/api/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, website }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "");

      if (hint) hint.textContent = "Subscribed. See you next issue.";
      if (emailInput) emailInput.value = "";
    } catch (err) {
      console.error(err);
      if (hint) hint.textContent = err.message || "Couldn’t subscribe right now. Try again.";
    }
  });
}

async function main() {
  initPage();
  initTipsModal();
  initNewsletter();

  let content;
  try {
    content = await loadContent();
  } catch (err) {
    console.error(err);
    showLoadError(articlesGrid);
    return;
  }
  const { articles, settings } = content;
  setTopbar(settings);

  const issueNum = settings.currentIssueNumber ?? null;
  $("#issueHeadline").textContent = `Issue #${issueNum ?? "—"}`;
  $("#issueDate").textContent = formatDate(settings.currentIssueDate, "long");

  // Home shows the current issue; if it has nothing yet, show everything
  let issueArticles = issueNum == null ? articles : articles.filter((a) => Number(a.issue) === Number(issueNum));
  if (!issueArticles.length) issueArticles = articles;

  const lead = pickLead(issueArticles);
  const leadRoot = $("#leadStory");

  function render() {
    const q = normalize(searchInput?.value);
    // While searching, show every match in the grid; otherwise the lead gets its own spot
    const showLead = Boolean(lead) && !q;
    leadRoot.replaceChildren(...(showLead ? [leadStory(lead)] : []));
    const list = issueArticles.filter((a) =>
      q ? normalize(`${a.title} ${a.summary} ${a.author} ${a.section}`).includes(q) : a !== lead
    );
    articlesGrid.replaceChildren(...sortArticles(list, sortSelect?.value || "newest").map(articleCard));
    emptyState.hidden = list.length !== 0 || showLead;
  }

  render();
  searchInput?.addEventListener("input", render);
  sortSelect?.addEventListener("change", render);
}

main();
