(() => {
  const status = document.getElementById('topology-status');
  const list = document.getElementById('topology-list');
  const graph = document.getElementById('topology-graph');
  const colors = new Set(['#64748b', '#2563eb', '#eab308', '#f97316', '#dc2626']);
  const types = new Set(['node', 'vm', 'ip']);

  async function load() {
    try {
      const response = await fetch('/api/topology', { credentials: 'same-origin' });
      if (!response.ok) throw new Error('Impossible de charger la topologie.');
      const data = await response.json();
      if (!Array.isArray(data.nodes) || !Array.isArray(data.edges)) throw new Error('Données de topologie invalides.');
      if (!data.nodes.length) {
        status.textContent = 'Aucun inventaire à afficher.';
        return;
      }
      if (typeof cytoscape !== 'function') throw new Error('Impossible de charger le graphe.');
      const labels = new Map();
      const nodes = data.nodes.map(node => {
        const id = String(node.id);
        const label = String(node.label);
        labels.set(id, label);
        const item = document.createElement('li');
        item.textContent = `${types.has(node.type) ? node.type : 'élément'} : ${label} — anomalie ${node.anomaly_severity || 'aucune'}`;
        list.append(item);
        return { data: { id, label, type: types.has(node.type) ? node.type : 'vm', color: colors.has(node.color) ? node.color : '#64748b' } };
      });
      const edges = data.edges.filter(edge => labels.has(String(edge.source)) && labels.has(String(edge.target))).map((edge, index) => {
        const item = document.createElement('li');
        item.textContent = `${labels.get(String(edge.source))} → ${labels.get(String(edge.target))}`;
        list.append(item);
        return { data: { id: `edge:${index}`, source: String(edge.source), target: String(edge.target) } };
      });
      cytoscape({
        container: graph,
        elements: [...nodes, ...edges],
        layout: { name: 'breadthfirst', directed: true, spacingFactor: 1.5, padding: 35 },
        style: [
          { selector: 'node', style: { label: 'data(label)', 'background-color': 'data(color)', color: '#172033', 'font-size': 13, 'text-outline-color': '#fff', 'text-outline-width': 2, 'text-wrap': 'wrap', 'text-max-width': '110px', width: 42, height: 42 } },
          { selector: 'node[type = "node"]', style: { shape: 'round-rectangle', width: 65 } },
          { selector: 'node[type = "ip"]', style: { shape: 'diamond' } },
          { selector: 'edge', style: { width: 2, 'line-color': '#475569', 'target-arrow-color': '#475569', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier' } }
        ]
      });
      status.textContent = `Topologie du run #${data.run_id} : ${nodes.length} éléments et ${edges.length} liens.`;
    } catch (error) {
      status.textContent = error.message;
    }
  }

  load();
})();
