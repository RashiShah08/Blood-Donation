// Public interactive widgets: blood compatibility explorer, quick eligibility check and mini maps.
(() => {
  "use strict";

  const { createMap, h } = window.BloodConnect;

  function describe(group, entry) {
    if (group === "O-") return "O- is the universal red cell donor: it can go to all 8 groups, but can only receive O-.";
    if (group === "AB+") return "AB+ is the universal red cell recipient: it can receive from all 8 groups, but can only give to AB+.";
    return `${group} can give to ${entry.give_to.length} groups and receive from ${entry.receive_from.length}.`;
  }

  document.querySelectorAll("[data-compat-explorer]").forEach((root) => {
    const compat = JSON.parse(root.dataset.compat);
    const buttons = [...root.querySelectorAll(".group-btn")];
    const summary = root.querySelector("[data-explorer-summary]");
    const flows = {
      give_to: root.querySelector('[data-flow="give_to"]'),
      receive_from: root.querySelector('[data-flow="receive_from"]'),
    };

    function select(group) {
      buttons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.group === group)));
      for (const [key, container] of Object.entries(flows)) {
        container.querySelectorAll(".chip-group").forEach((chip) => {
          chip.classList.toggle("is-match", compat[group][key].includes(chip.dataset.group));
        });
      }
      summary.textContent = describe(group, compat[group]);
    }

    buttons.forEach((button, index) => {
      button.addEventListener("click", () => {
        button.focus();  // Safari doesn't focus buttons on click; arrow keys must continue from here.
        select(button.dataset.group);
      });
      // Arrow keys move around the 4 x 2 grid of blood groups.
      button.addEventListener("keydown", (event) => {
        const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 4, ArrowUp: -4 };
        if (!(event.key in moves)) return;
        event.preventDefault();
        const next = buttons[(index + moves[event.key] + buttons.length) % buttons.length];
        next.focus();
        select(next.dataset.group);
      });
    });
  });

  document.querySelectorAll("[data-quick-check]").forEach((root) => {
    const rules = JSON.parse(root.dataset.rules);
    const form = root.querySelector("[data-quick-form]");
    const verdict = root.querySelector("[data-verdict]");
    const title = root.querySelector("[data-verdict-title]");
    const reasonsList = root.querySelector("[data-verdict-reasons]");
    const note = root.querySelector("[data-verdict-note]");
    const icons = root.querySelectorAll("[data-verdict-icon]");

    function evaluate() {
      const fields = form.elements;
      const age = Number(fields.age.value);
      const weight = Number(fields.weight.value);
      const gender = fields.gender.value;
      const days = fields.days.value === "" ? null : Number(fields.days.value);
      form.querySelector('[data-output="age"]').textContent = String(age);
      form.querySelector('[data-output="weight"]').textContent = String(weight);

      const reasons = [];
      if (age < rules.min_age) {
        const years = rules.min_age - age;
        reasons.push(`Donors must be at least ${rules.min_age}: that's ${years} more ${years === 1 ? "year" : "years"}.`);
      }
      if (age > rules.max_age) reasons.push(`Donors must be ${rules.max_age} or younger.`);
      if (weight < rules.min_weight) reasons.push(`Donors must weigh at least ${rules.min_weight} kg.`);
      const interval = rules.interval_days[gender] ?? rules.interval_days.Other;
      if (days !== null && days < interval) {
        reasons.push(`Leave ${interval} days between donations: about ${interval - days} more days to go.`);
      }

      const ok = reasons.length === 0;
      verdict.dataset.state = ok ? "ok" : "wait";
      icons.forEach((icon) => {
        icon.hidden = icon.dataset.verdictIcon !== (ok ? "ok" : "wait");
      });
      title.textContent = ok ? "You could likely donate" : "Not just yet";
      reasonsList.replaceChildren(...reasons.map((reason) => h("li", {}, reason)));
      reasonsList.hidden = ok;
      note.textContent = ok
        ? "You'll answer a short health questionnaire before each pledge."
        : "You can still register now. We'll only show you requests once you're able to donate.";
    }

    form.addEventListener("input", evaluate);
    form.addEventListener("change", evaluate);
    evaluate();
  });

  document.querySelectorAll("[data-mini-map]").forEach((element) => {
    const hospital = [parseFloat(element.dataset.hospitalLat), parseFloat(element.dataset.hospitalLng)];
    const home = [parseFloat(element.dataset.homeLat), parseFloat(element.dataset.homeLng)];
    const map = createMap(element, hospital, 13);
    if (!map) return;
    window.L.marker(hospital, { title: element.dataset.hospitalName }).addTo(map).bindTooltip(element.dataset.hospitalName);
    const homeIcon = window.L.divIcon({ className: "donor-marker", iconSize: [18, 18] });
    window.L.marker(home, { icon: homeIcon, title: "Your home area" }).addTo(map).bindTooltip("Your home area");
    window.L.polyline([home, hospital], { color: "#d42a40", weight: 3, dashArray: "6 8" }).addTo(map);
    map.fitBounds(window.L.latLngBounds([home, hospital]), { padding: [36, 36], maxZoom: 15 });
  });
  // Home hero: a looping four-step story (request, match, pledge, arrival). Pauses when off screen.
  const stage = document.querySelector("[data-hero-stage]");
  if (stage) {
    const STEP_MS = 2800;
    const eta = stage.querySelector("[data-stage-eta]");
    const motion = stage.querySelector("animateMotion");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    stage.style.setProperty("--stage-step", `${STEP_MS}ms`);
    let phase = 0;
    let timer = null;
    let etaTimer = null;

    function show(next) {
      phase = next;
      stage.dataset.phase = String(phase);
      clearInterval(etaTimer);
      if (phase === 2) {
        let minutes = 6;
        eta.textContent = minutes;
        etaTimer = setInterval(() => {
          minutes = Math.max(1, minutes - 1);
          eta.textContent = minutes;
        }, STEP_MS / 6);
      }
      if (phase === 3) {
        eta.textContent = "0";
        if (motion && motion.beginElement) motion.beginElement();
      }
    }

    function start() {
      if (timer || reduce) return;
      timer = setInterval(() => show((phase + 1) % 4), STEP_MS);
    }
    function stop() {
      clearInterval(timer);
      timer = null;
    }

    if (reduce) {
      show(2);
    } else {
      new IntersectionObserver(([entry]) => (entry.isIntersecting ? start() : stop())).observe(stage);
      document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
    }
  }
  // Sections that draw in once scrolled into view. They render fully without JavaScript.
  const reveals = document.querySelectorAll("[data-reveal]");
  if (reveals.length && "IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.3 });
    reveals.forEach((element) => {
      if (element.getBoundingClientRect().top > window.innerHeight) {
        element.classList.add("is-armed");
        observer.observe(element);
      }
    });
  }
})();
