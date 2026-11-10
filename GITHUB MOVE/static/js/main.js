// Global Main JS for BEA AI Assessment System
document.addEventListener("DOMContentLoaded", function () {
  // Auto-dismiss alerts after 6 seconds
  const alerts = document.querySelectorAll(".alert");
  alerts.forEach(function (alert) {
    setTimeout(function () {
      alert.style.transition = "opacity 0.5s ease";
      alert.style.opacity = "0";
      setTimeout(function () { alert.remove(); }, 500);
    }, 6000);
  });
});

function showToast(message, type = "info") {
  const container = document.querySelector(".flash-container") || createToastContainer();
  const alert = document.createElement("div");
  alert.className = `alert alert-${type}`;
  alert.innerHTML = `<span>${message}</span><button onclick="this.parentElement.remove()" style="background:none;border:none;color:inherit;cursor:pointer;font-size:1.1rem;">&times;</button>`;
  container.appendChild(alert);
  setTimeout(function () { alert.remove(); }, 5000);
}

function createToastContainer() {
  const main = document.querySelector(".container");
  const div = document.createElement("div");
  div.className = "flash-container";
  main.insertBefore(div, main.firstChild);
  return div;
}
