// Minimal JS — most interactivity is inline in templates.
// This file handles any cross-page utilities.

document.addEventListener("DOMContentLoaded", () => {
  // Auto-dismiss flash messages after 6 s
  document.querySelectorAll(".flash").forEach(el => {
    setTimeout(() => el.remove(), 6000);
  });
});
