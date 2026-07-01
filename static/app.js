document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.classList.contains("loading-form")) return;
  const button = form.querySelector("button[type='submit']");
  if (!button) return;
  button.disabled = true;
  button.dataset.originalText = button.textContent;
  button.textContent = "Refreshing...";
});
