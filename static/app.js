document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.classList.contains("loading-form")) return;
  const button = form.querySelector("button[type='submit']");
  if (!button) return;
  button.disabled = true;
  button.dataset.originalText = button.textContent;
  button.textContent = "Refreshing...";
});

document.querySelectorAll("[data-auth-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    const target = tab.dataset.authTab;
    document.querySelectorAll("[data-auth-tab]").forEach((item) => item.classList.toggle("active", item === tab));
    document.querySelectorAll("[data-auth-panel]").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.authPanel === target);
    });
  });
});

document.querySelectorAll("[data-table-filter]").forEach((input) => {
  input.addEventListener("input", () => {
    const table = document.querySelector(input.dataset.tableFilter);
    if (!table) return;
    const query = input.value.toLowerCase();
    table.querySelectorAll("tbody tr").forEach((row) => {
      row.hidden = query && !row.textContent.toLowerCase().includes(query);
    });
  });
});
