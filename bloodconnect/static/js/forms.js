// Live form helpers: password strength, completeness checklist, donor card and request previews.
(() => {
  "use strict";

  const { h } = window.BloodConnect;

  document.querySelectorAll("[data-strength-for]").forEach((meter) => {
    const input = document.getElementById(meter.dataset.strengthFor);
    if (!input) return;
    const text = meter.querySelector("[data-strength-text]");
    const labels = { 1: "Weak", 2: "Fair", 3: "Good", 4: "Strong" };
    input.addEventListener("input", () => {
      const value = input.value;
      meter.hidden = value.length === 0;
      let score = 0;
      if (value.length >= 8) score += 1;
      if (value.length >= 12) score += 1;
      if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
      if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1;
      if (value.length < 8) score = 1;
      score = Math.max(1, Math.min(4, score));
      meter.dataset.score = String(score);
      text.textContent =
        value.length < 8 ? `${8 - value.length} more ${8 - value.length === 1 ? "character" : "characters"} needed` : `${labels[score]} password`;
    });
  });

  document.querySelectorAll("[data-completeness]").forEach((panel) => {
    const form = document.getElementById(panel.dataset.completeness);
    if (!form) return;
    const steps = [...panel.querySelectorAll("li[data-fields]")];
    const count = panel.querySelector("[data-completeness-count]");
    const bar = panel.querySelector("[data-completeness-bar]");

    function filled(name) {
      const field = form.elements[name];
      if (!field) return false;
      if (field.type === "checkbox") return field.checked;
      if (name === "password") return field.value.length >= 8;
      if (name === "confirm_password") return field.value.length >= 8 && field.value === form.elements.password.value;
      return field.value.trim() !== "";
    }

    function update() {
      let done = 0;
      steps.forEach((step) => {
        const complete = step.dataset.fields.split(" ").every(filled);
        step.classList.toggle("is-done", complete);
        if (complete) done += 1;
      });
      count.textContent = `${done} of ${steps.length} done`;
      bar.value = done;
    }

    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
  });

  const card = document.querySelector("[data-donor-card]");
  const registerForm = document.getElementById("register-form");
  if (card && registerForm) {
    const giveTo = JSON.parse(card.dataset.giveTo);
    const slot = (key) => card.querySelector(`[data-card="${key}"]`);
    let lastGroup = null;

    function ageFrom(value) {
      if (!value) return null;
      const birth = new Date(value);
      if (Number.isNaN(birth.getTime())) return null;
      const now = new Date();
      let age = now.getFullYear() - birth.getFullYear();
      if (now.getMonth() < birth.getMonth() || (now.getMonth() === birth.getMonth() && now.getDate() < birth.getDate())) age -= 1;
      return age >= 0 && age < 130 ? age : null;
    }

    function update() {
      const fields = registerForm.elements;
      slot("name").textContent = fields.name.value.trim() || "Your name";
      const group = fields.blood_group.value;
      const groupSlot = slot("blood_group");
      groupSlot.textContent = group || "?";
      if (group && group !== lastGroup) {
        groupSlot.classList.remove("is-updated");
        void groupSlot.offsetWidth;
        groupSlot.classList.add("is-updated");
      }
      lastGroup = group;
      const age = ageFrom(fields.date_of_birth.value);
      slot("age").textContent = age === null ? "Age —" : `Age ${age}`;
      slot("weight").textContent = fields.weight_kg.value ? `${fields.weight_kg.value} kg` : "Weight —";
      slot("location").textContent = fields.latitude.value && fields.longitude.value ? "Home area set" : "No home area yet";
      const chips = slot("give_to");
      chips.replaceChildren(
        ...(group && giveTo[group] ? giveTo[group].map((target) => h("span", { class: "chip-group" }, target)) : [h("span", { class: "chip-group" }, "Pick a blood group")]),
      );
    }

    registerForm.addEventListener("input", update);
    registerForm.addEventListener("change", update);
    update();
  }

  const preview = document.querySelector("[data-request-preview]");
  const requestForm = document.getElementById("request-form");
  if (preview && requestForm) {
    const compat = JSON.parse(preview.dataset.compat);
    const slot = (key) => preview.querySelector(`[data-preview="${key}"]`);
    const urgencyLabels = { critical: "Critical urgency", high: "High urgency", medium: "Medium urgency", low: "Low urgency" };

    function update() {
      const fields = requestForm.elements;
      const group = fields.blood_group.value;
      const urgency = fields.urgency.value;
      const units = Number(fields.units_required.value);
      slot("blood_group").textContent = group || "—";
      const badge = slot("urgency");
      badge.textContent = urgencyLabels[urgency] || "";
      badge.hidden = !urgency;
      badge.className = `badge badge-urgency-${urgency || "high"}`;
      slot("units").textContent = units > 0 ? `${units} ${units === 1 ? "unit" : "units"}` : "Units";
      slot("compat")
        .querySelectorAll(".chip-group")
        .forEach((chip) => chip.classList.toggle("is-match", Boolean(group) && compat[group].includes(chip.dataset.group)));
      slot("compat-note").textContent = group
        ? `${compat[group].length} of 8 blood groups can donate to a ${group} patient.`
        : "Choose a blood group to see which donors will be alerted.";
    }

    requestForm.addEventListener("input", update);
    requestForm.addEventListener("change", update);
    update();
  }
})();
