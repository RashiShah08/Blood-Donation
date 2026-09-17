// Eligibility questionnaire: keyboard answers (Y/N), auto-advance, progress bar and ring.
(() => {
  "use strict";

  const form = document.querySelector("form[data-questionnaire]");
  if (!form) return;

  const { toast, reduceMotion } = window.BloodConnect;
  const counter = form.querySelector("[data-progress-count]");
  const progress = form.querySelector("progress");
  const ring = document.querySelector("[data-ring]");
  const ringValue = document.querySelector("[data-ring-value]");
  const items = [...form.querySelectorAll(".questions > li")];
  const names = items.map((item) => item.querySelector('input[type="radio"]').name);
  let current = 0;

  const answered = (index) => Boolean(form.querySelector(`input[name="${names[index]}"]:checked`));
  const firstUnanswered = (after = -1) => names.findIndex((_, index) => index > after && !answered(index));

  function update() {
    const count = names.filter((_, index) => answered(index)).length;
    counter.textContent = String(count);
    progress.value = count;
    items.forEach((item, index) => item.classList.toggle("is-answered", answered(index)));
    if (ring) {
      ring.style.setProperty("--value", String(Math.round((count / names.length) * 100)));
      ringValue.textContent = `${count}/${names.length}`;
    }
  }

  function setCurrent(index, { scroll = true } = {}) {
    current = Math.max(0, Math.min(items.length - 1, index));
    items.forEach((item, position) => item.classList.toggle("is-current", position === current));
    if (scroll) items[current].scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
  }

  form.addEventListener("change", (event) => {
    update();
    const index = names.indexOf(event.target.name);
    if (index === -1) return;
    const next = firstUnanswered(index);
    if (next !== -1) setCurrent(next);
    else setCurrent(index, { scroll: false });
  });

  form.addEventListener("focusin", (event) => {
    const item = event.target.closest(".questions > li");
    if (item) setCurrent(items.indexOf(item), { scroll: false });
  });

  document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.target.matches("input[type=text], textarea, select")) return;
    const key = event.key.toLowerCase();
    if (key === "y" || key === "n") {
      const input = form.querySelector(`input[name="${names[current]}"][value="${key === "y" ? "yes" : "no"}"]`);
      input.checked = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      event.preventDefault();
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !event.target.matches("input[type=radio]")) {
      setCurrent(current + (event.key === "ArrowDown" ? 1 : -1));
      event.preventDefault();
    }
  });

  form.addEventListener("submit", (event) => {
    const missing = names.filter((_, index) => !answered(index));
    if (missing.length === 0) return;
    event.preventDefault();
    const index = names.indexOf(missing[0]);
    setCurrent(index);
    form.querySelector(`input[name="${missing[0]}"]`).focus({ preventScroll: true });
    toast(
      `Please answer ${missing.length === 1 ? "the remaining question" : `all ${missing.length} remaining questions`}.`,
      "error",
    );
  });

  update();
  setCurrent(Math.max(0, firstUnanswered()), { scroll: false });
})();
