/* CloudInventory — scripts des pages (CSP `script-src 'self'` : aucun script inline).

   Trois blocs indépendants, actifs seulement si leur repère existe dans la page :
   1. thème clair/sombre (toutes les pages, cahier §11.1) ;
   2. statistiques + graphiques (dashboard, chargés en AJAX depuis /ajax/stats) ;
   3. lancement d'un run en AJAX (dashboard, POST /ajax/run avec jeton CSRF).
   Toute donnée affichée passe par textContent/createTextNode : jamais innerHTML. */
(function () {
  "use strict";

  /* --- 1. Thème ---------------------------------------------------------- */
  var toggle = document.getElementById("themeToggle");
  var themeIcon = document.getElementById("themeIcon");

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    try {
      localStorage.setItem("ci-theme", theme);
    } catch (error) {
      /* stockage de navigateur indisponible : la page garde le thème appliqué */
    }
    if (themeIcon) {
      themeIcon.className = theme === "dark" ? "bi bi-sun-fill" : "bi bi-moon-fill";
    }
  }

  var savedTheme = "light";
  try {
    savedTheme = localStorage.getItem("ci-theme") || "light";
  } catch (error) {
    savedTheme = "light";
  }
  applyTheme(savedTheme);

  if (toggle) {
    toggle.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-bs-theme");
      applyTheme(current === "dark" ? "light" : "dark");
    });
  }

  /* --- 2 et 3. Dashboard -------------------------------------------------- */
  var dashboard = document.getElementById("dashboard");
  if (!dashboard) {
    return;
  }

  var statsUrl = dashboard.getAttribute("data-stats-url");
  var runUrl = dashboard.getAttribute("data-run-url");
  var csrfToken = dashboard.getAttribute("data-csrf");

  var runButton = document.getElementById("btn-run");
  var runLoading = document.getElementById("run-loading");
  var runResult = document.getElementById("run-result");
  var chartsError = document.getElementById("charts-error");

  function showChartsError(message) {
    if (chartsError) {
      chartsError.textContent = message;
      chartsError.classList.remove("d-none");
    }
  }

  function drawCharts(data) {
    if (typeof Chart === "undefined") {
      showChartsError("Chart.js introuvable : graphiques indisponibles.");
      return;
    }

    var chartOptions = {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 12, padding: 10, font: { size: 11 } }
        }
      }
    };

    new Chart(document.getElementById("chartMatch"), {
      type: "doughnut",
      data: {
        labels: ["Matched (nom)", "Matched (FQDN)", "Matched (IP)", "No Match"],
        datasets: [{
          data: [
            data.match.matched_name,
            data.match.matched_fqdn,
            data.match.matched_ip,
            data.match.no_match
          ],
          backgroundColor: ["#198754", "#6f42c1", "#0dcaf0", "#dc3545"],
          borderWidth: 0
        }]
      },
      options: chartOptions
    });

    var anomalyLabels = Object.keys(data.anomalies);
    var anomalyValues = Object.keys(data.anomalies).map(function (code) {
      return data.anomalies[code];
    });
    var anomalyColors = anomalyLabels.map(function (_code, index) {
      return ["#dc3545", "#ffc107", "#0d6efd", "#6f42c1", "#fd7e14"][index % 5];
    });
    // Cahier §8.1 : « Barres : anomalies par type ».
    new Chart(document.getElementById("chartAnomalies"), {
      type: "bar",
      data: {
        labels: anomalyLabels.length ? anomalyLabels : ["Aucune"],
        datasets: [{
          label: "Anomalies",
          data: anomalyValues.length ? anomalyValues : [0],
          backgroundColor: anomalyValues.length ? anomalyColors : ["#dee2e6"],
          borderWidth: 0
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
      }
    });

    new Chart(document.getElementById("chartEvolution"), {
      type: "line",
      data: {
        labels: data.evolution.labels,
        datasets: [
          {
            label: "Matched (nom)",
            data: data.evolution.matched_name,
            borderColor: "#198754",
            backgroundColor: "rgba(25,135,84,0.1)",
            fill: true, tension: 0.3, pointRadius: 4
          },
          {
            label: "Matched (FQDN)",
            data: data.evolution.matched_fqdn,
            borderColor: "#6f42c1",
            backgroundColor: "rgba(111,66,193,0.1)",
            fill: true, tension: 0.3, pointRadius: 4
          },
          {
            label: "Matched (IP)",
            data: data.evolution.matched_ip,
            borderColor: "#0dcaf0",
            backgroundColor: "rgba(13,202,240,0.1)",
            fill: true, tension: 0.3, pointRadius: 4
          },
          {
            label: "No Match",
            data: data.evolution.no_match,
            borderColor: "#dc3545",
            backgroundColor: "rgba(220,53,69,0.1)",
            fill: true, tension: 0.3, pointRadius: 4
          }
        ]
      },
      options: Object.assign({}, chartOptions, {
        scales: { y: { beginAtZero: true } }
      })
    });
  }

  function loadStats() {
    fetch(statsUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("statistiques indisponibles (HTTP " + response.status + ")");
        }
        return response.json();
      })
      .then(function (data) {
        if (data && data.has_data) {
          drawCharts(data);
        }
      })
      .catch(function (error) {
        showChartsError("Statistiques indisponibles : " + error.message + ".");
      });
  }

  function describeRun(run) {
    if (run.status === "SUCCESS") {
      return "Run #" + run.id + " — " + run.matched_name_count +
        " VMs appariées par nom, " + run.matched_fqdn_count + " par FQDN, " +
        run.matched_ip_count + " par IP, " + run.no_match_count +
        " sans correspondance.";
    }
    return "Run #" + run.id + " en échec : " +
      (run.error_message || "erreur inconnue") + ".";
  }

  function announce(kind, text) {
    runResult.className = kind === "success" ? "alert alert-success" : "alert alert-danger";
    runResult.textContent = text;
    runResult.classList.remove("d-none");
  }

  function launchRun() {
    runButton.disabled = true;
    runResult.classList.add("d-none");
    runResult.textContent = "";
    runLoading.classList.remove("d-none");

    fetch(runUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest"
      },
      body: "csrf_token=" + encodeURIComponent(csrfToken)
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("lancement refusé (HTTP " + response.status + ")");
        }
        return response.json();
      })
      .then(function (run) {
        runLoading.classList.add("d-none");
        announce(run.status === "SUCCESS" ? "success" : "danger", describeRun(run));
        if (run.status === "SUCCESS") {
          window.setTimeout(function () {
            window.location.reload();
          }, 2000);
        }
      })
      .catch(function (error) {
        runLoading.classList.add("d-none");
        announce("danger", "Échec du lancement : " + error.message + ".");
      })
      .finally(function () {
        runButton.disabled = false;
      });
  }

  if (runButton) {
    runButton.addEventListener("click", launchRun);
  }

  loadStats();
})();
