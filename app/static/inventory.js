(() => {
  const form = document.getElementById('inventory-filters');
  const rows = document.getElementById('inventory-rows');
  const message = document.getElementById('inventory-message');
  const exportLink = document.getElementById('inventory-export');
  const excelExportLink = document.getElementById('inventory-export-excel');
  const previous = document.getElementById('inventory-prev');
  const next = document.getElementById('inventory-next');
  const pageLabel = document.getElementById('inventory-page');
  const columns = ['vm_id', 'vm_name', 'type', 'node', 'status_final', 'ip_final', 'dns_final', 'match_status', 'role'];
  let page = 1;
  let requestId = 0;
  let timer;

  function filters() {
    const params = new URLSearchParams(new FormData(form));
    for (const [key, value] of params) {
      if (!value) params.delete(key);
    }
    return params;
  }

  async function load() {
    if (!form.reportValidity()) return;
    const id = ++requestId;
    const params = filters();
    exportLink.href = `/inventory/export?${params}`;
    excelExportLink.href = `/inventory/export.xlsx?${params}`;
    params.set('page', String(page));
    params.set('per_page', '25');
    message.textContent = 'Chargement de l’inventaire…';
    try {
      const response = await fetch(`/inventory/data?${params}`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error('La récupération de l’inventaire a échoué.');
      const data = await response.json();
      if (id !== requestId) return;
      rows.replaceChildren();
      for (const item of data.items) {
        const row = document.createElement('tr');
        for (const field of columns) {
          const cell = document.createElement('td');
          if (field === 'vm_id' && item.asset_id != null) {
            const link = document.createElement('a');
            link.href = `/assets/${encodeURIComponent(item.asset_id)}`;
            link.textContent = item[field] == null ? '' : String(item[field]);
            cell.append(link);
          } else {
            cell.textContent = item[field] == null ? '' : String(item[field]);
          }
          row.append(cell);
        }
        rows.append(row);
      }
      const pages = Math.max(1, Math.ceil(data.total / 25));
      pageLabel.textContent = `Page ${page} sur ${pages}`;
      previous.disabled = page <= 1;
      next.disabled = page >= pages;
      message.textContent = data.total ? `${data.total} asset(s) trouvé(s).` : 'Aucun asset trouvé.';
    } catch (error) {
      if (id !== requestId) return;
      rows.replaceChildren();
      previous.disabled = true;
      next.disabled = true;
      pageLabel.textContent = '';
      message.textContent = error.message;
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    page = 1;
    load();
  });
  form.addEventListener('input', () => {
    clearTimeout(timer);
    page = 1;
    timer = setTimeout(load, 300);
  });
  form.addEventListener('change', () => { page = 1; load(); });
  previous.addEventListener('click', () => { page -= 1; load(); });
  next.addEventListener('click', () => { page += 1; load(); });
  load();
})();
