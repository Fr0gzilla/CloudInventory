(() => {
  const message = document.getElementById('dashboard-message');
  const runMessage = document.getElementById('dashboard-run-message');
  const form = document.getElementById('dashboard-run-form');
  const codes = ['MATCHED_NAME', 'MATCHED_FQDN', 'MATCHED_IP', 'NO_MATCH'];
  const charts = [];

  function draw(id, type, labels, values) {
    const canvas = document.getElementById(id);
    charts.push(new Chart(canvas, {
      type,
      data: { labels, datasets: [{ label: 'Nombre', data: values,
        backgroundColor: ['#1769aa', '#16734a', '#a85410', '#a32432', '#62459a', '#25677a'],
        borderColor: '#1769aa' }] },
      options: { responsive: true, plugins: { legend: { display: type === 'doughnut' } },
        scales: type === 'doughnut' ? {} : { y: { beginAtZero: true, ticks: { precision: 0 } } } }
    }));
  }

  async function load() {
    message.textContent = 'Chargement des graphiques…';
    try {
      const response = await fetch('/dashboard/data', { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Impossible de charger les graphiques.');
      const data = await response.json();
      charts.splice(0).forEach(chart => chart.destroy());
      const matches = codes.map(code => data.stats.matches[code] || 0);
      const anomalyLabels = Object.keys(data.stats.anomalies).sort();
      const history = data.runs.slice(0, 10).reverse();
      draw('dashboard-matches', 'doughnut', codes, matches);
      draw('dashboard-anomalies', 'bar', anomalyLabels, anomalyLabels.map(code => data.stats.anomalies[code]));
      draw('dashboard-history', 'line', history.map(run => `#${run.id}`),
        history.map(run => (run.matched_name_count || 0) + (run.matched_fqdn_count || 0) + (run.matched_ip_count || 0)));
      message.textContent = data.stats.run ? 'Graphiques du dernier run chargés.' : 'Aucun inventaire à afficher.';
    } catch (error) {
      message.textContent = error.message;
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('button');
    button.disabled = true;
    runMessage.textContent = 'Inventaire en cours…';
    try {
      const response = await fetch(form.action, { method: 'POST', credentials: 'same-origin', body: new FormData(form) });
      if (!response.ok) throw new Error('Impossible de lancer l’inventaire.');
      const run = await response.json();
      runMessage.textContent = `Inventaire #${run.id} terminé : ${run.status}.`;
      window.location.reload();
    } catch (error) {
      runMessage.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  load();
})();
