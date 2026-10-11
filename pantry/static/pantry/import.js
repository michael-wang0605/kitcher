const progress = document.querySelector("[data-import-progress]");
const form = document.querySelector("[data-import-edit]");
let editing = false;
form?.addEventListener("input", () => { editing = true; });
if (progress) setTimeout(() => { if (!editing && !document.hidden) location.reload(); }, 10000);
