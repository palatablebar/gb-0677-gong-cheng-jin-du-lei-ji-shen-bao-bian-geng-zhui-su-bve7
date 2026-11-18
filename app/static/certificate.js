const notice = document.getElementById('notice');

function submitCertificate(path, data) {
  notice.textContent = '计量中…';
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
    .then(async (response) => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.message);
      // 同一输入与处理规则已有结论：直接复用，不新增版本。
      if (body.reused && !body.is_current) {
        notice.textContent = '该输入与处理规则已有结论（已被替代），未新增版本。如需恢复请在该版本上切换。';
        return;
      }
      window.location.reload();
    })
    .catch((error) => (notice.textContent = error.message));
}

document.getElementById('calculate').addEventListener('click', function () {
  submitCertificate('/api/runs', { sample: document.getElementById('dataset').value });
});

document.getElementById('approve')?.addEventListener('click', function () {
  submitCertificate('/api/runs/' + certificate.id + '/review', { version: certificate.version, decision: '已复核' });
});

document.getElementById('print').addEventListener('click', () => window.print());

document.getElementById('attach').addEventListener('click', async () => {
  const file = document.getElementById('attachment').files[0];
  if (!file) {
    notice.textContent = '请选择申报文件。';
    return;
  }
  const form = new FormData();
  form.append('file', file);
  notice.textContent = '导入中…';
  try {
    const response = await fetch('/api/upload', { method: 'POST', body: form });
    const body = await response.json();
    if (!response.ok) throw Error(body.message);
    location.reload();
  } catch (error) {
    notice.textContent = error.message;
  }
});

document.getElementById('revert')?.addEventListener('click', async () => {
  const revisionNotice = document.getElementById('revision-notice');
  revisionNotice.textContent = '切换中…';
  try {
    const response = await fetch('/api/runs/' + certificate.id + '/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version: certificate.version }),
    });
    const body = await response.json();
    if (!response.ok) throw Error(body.message);
    location.reload();
  } catch (error) {
    revisionNotice.textContent = error.message;
  }
});

document.getElementById('lookup')?.addEventListener('input', function () {
  document.querySelectorAll('#valuation-rows tr').forEach((row) => (row.hidden = !row.textContent.includes(this.value)));
});
