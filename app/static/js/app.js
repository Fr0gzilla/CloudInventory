/* CloudInventory — scripts des pages (CSP `script-src 'self'` : aucun script inline).

   Quatre blocs indépendants, actifs seulement si leur repère existe dans la page :
   1. thème clair/sombre (toutes les pages, cahier §11.1) ;
   2. recherche AJAX de l'inventaire (page inventaire, GET /ajax/inventory/search) ;
   3. statistiques + graphiques (dashboard, chargés en AJAX depuis /ajax/stats) ;
   4. lancement d'un run en AJAX (dashboard, POST /ajax/run avec jeton CSRF).
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

  /* --- 2. Inventaire : recherche live ------------------------------------ */
  var inventory = document.getElementById("inventory");
  if (inventory) {
    var searchUrl = inventory.getAttribute("data-search-url");
    var resetUrl = inventory.getAttribute("data-reset-url");
    var filterForm = document.getElementById("filterForm");
    var searchInput = document.getElementById("liveSearch");
    var tbody = document.getElementById("inventoryBody");
    var countEl = document.getElementById("inventory-count");
    var searchError = document.getElementById("inventory-error");
    var paginationNav = document.getElementById("inventory-pagination");
    var searchTimer = null;

    function dash(value) {
      return value === null || value === undefined || value === "" ? "\u2014" : String(value);
    }

    function percent(value) {
      return value === null || value === undefined ? "\u2014" : value + "%";
    }

    function textCell(value, className) {
      var cell = document.createElement("td");
      if (className) {
        cell.className = className;
      }
      cell.textContent = dash(value);
      return cell;
    }

    function matchRowClass(status) {
      if (status === "NO_MATCH") {
        return "table-danger";
      }
      if (status === "MATCHED_IP") {
        return "table-info";
      }
      if (status === "MATCHED_FQDN") {
        return "table-warning";
      }
      if (status === "MATCHED_NAME") {
        return "table-success";
      }
      return "";
    }

    function matchBadge(status) {
      var badge = document.createElement("span");
      if (status === "MATCHED_NAME") {
        badge.className = "badge bg-primary";
        badge.textContent = "MATCHED (nom)";
      } else if (status === "MATCHED_FQDN") {
        badge.className = "badge ci-badge-purple";
        badge.textContent = "MATCHED (FQDN)";
      } else if (status === "MATCHED_IP") {
        badge.className = "badge bg-info text-dark";
        badge.textContent = "MATCHED (IP)";
      } else if (status === "NO_MATCH") {
        badge.className = "badge bg-danger";
        badge.textContent = "NO MATCH";
      } else {
        badge.className = "badge bg-secondary";
        badge.textContent = dash(status);
      }
      return badge;
    }

    function badgeCell(value, className, badgeClass) {
      var cell = document.createElement("td");
      if (className) {
        cell.className = className;
      }
      var badge = document.createElement("span");
      badge.className = badgeClass;
      badge.textContent = dash(value);
      cell.appendChild(badge);
      return cell;
    }

    function buildRow(item) {
      var row = document.createElement("tr");
      row.className = matchRowClass(item.match_status);

      var nameCell = document.createElement("td");
      var nameLink = document.createElement("a");
      nameLink.href = item.detail_url;
      nameLink.className = "fw-semibold text-decoration-none";
      nameLink.textContent = item.vm_name;
      nameCell.appendChild(nameLink);
      row.appendChild(nameCell);

      row.appendChild(textCell(item.node, "d-none d-md-table-cell"));
      row.appendChild(badgeCell(item.status, "", item.status === "running" ? "badge bg-success" : "badge bg-secondary"));
      row.appendChild(badgeCell(item.type, "d-none d-lg-table-cell", "badge bg-info text-dark"));

      var ipCell = document.createElement("td");
      var ipValue = document.createElement("code");
      ipValue.className = "small";
      ipValue.textContent = dash(item.ip);
      ipCell.appendChild(ipValue);
      row.appendChild(ipCell);

      row.appendChild(textCell(item.dns, "d-none d-md-table-cell"));
      row.appendChild(textCell(percent(item.cpu), "d-none d-lg-table-cell"));
      row.appendChild(textCell(percent(item.ram_pct), "d-none d-lg-table-cell"));
      row.appendChild(textCell(percent(item.disk_pct), "d-none d-xl-table-cell"));
      row.appendChild(textCell(item.uptime, "d-none d-xl-table-cell"));

      var matchCell = document.createElement("td");
      matchCell.appendChild(matchBadge(item.match_status));
      row.appendChild(matchCell);

      row.appendChild(textCell(item.source, "d-none d-xl-table-cell"));
      row.appendChild(textCell(item.role, "d-none d-xl-table-cell"));
      return row;
    }

    function renderRows(items) {
      while (tbody.firstChild) {
        tbody.removeChild(tbody.firstChild);
      }
      if (!items.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = 13;
        emptyCell.className = "text-muted text-center py-3";
        emptyCell.textContent = "Aucun résultat";
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
        return;
      }
      items.forEach(function (item) {
        tbody.appendChild(buildRow(item));
      });
    }

    function searchQueryString() {
      var params = new URLSearchParams();
      Array.prototype.forEach.call(filterForm.elements, function (field) {
        if (field.name && field.value) {
          params.set(field.name, field.value);
        }
      });
      return params.toString();
    }

    function runSearch() {
      searchError.classList.add("d-none");
      searchError.textContent = "";
      fetch(searchUrl + "?" + searchQueryString(), {
        credentials: "same-origin",
        headers: { Accept: "application/json" }
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("HTTP " + response.status);
          }
          return response.json();
        })
        .then(function (data) {
          renderRows(data.items);
          var message = data.total + " résultat(s)";
          if (data.total > data.items.length) {
            message += " — affichage limité à " + data.items.length + " lignes";
          }
          countEl.textContent = message;
          if (paginationNav) {
            paginationNav.classList.add("d-none");
          }
        })
        .catch(function (error) {
          searchError.textContent = "Recherche indisponible : " + error.message + ".";
          searchError.classList.remove("d-none");
        });
    }

    if (filterForm && searchInput && tbody) {
      searchInput.addEventListener("input", function () {
        window.clearTimeout(searchTimer);
        var value = searchInput.value.trim();
        if (value.length === 0) {
          // Requête vidée : retour à l'état serveur (filtres conservés, pagination restaurée).
          window.location.href = resetUrl;
          return;
        }
        if (value.length < 2) {
          return;
        }
        searchTimer = window.setTimeout(runSearch, 300);
      });
    }
  }

  /* --- 3 et 4. Dashboard -------------------------------------------------- */
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
