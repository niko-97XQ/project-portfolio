(() => {
  const form = document.getElementById('appointmentForm');
  const dialog = document.getElementById('appointmentDialog');
  const status = document.getElementById('appointmentStatus');
  const submit = form.querySelector('button[type="submit"]');
  const result = document.getElementById('appointmentResult');
  const dateInput = document.getElementById('appointmentDate');
  const controls = [...form.elements];
  const format = new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
  dateInput.min = format.format(new Date());
  dateInput.max = format.format(new Date(Date.now() + 365 * 86400000));
  let requestKey = '', previousPayload = '', busy = false;
  const openers = document.querySelectorAll('a[href="#contact"],a[href="#appointmentForm"]');
  function reflectDialogState() {
    document.documentElement.classList.toggle('appointment-dialog-open',dialog.open);
    openers.forEach(link => link.setAttribute('aria-expanded',String(dialog.open)));
  }
  function revealNotice(element) {
    if (!dialog.open) return;
    element.focus({preventScroll:true});
    const top = Math.max(0,dialog.scrollTop + element.getBoundingClientRect().top - dialog.getBoundingClientRect().top - 80);
    dialog.scrollTo({top,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  }
  openers.forEach(link => {
    link.setAttribute('aria-haspopup','dialog');
    link.setAttribute('aria-controls','appointmentDialog');
    link.setAttribute('aria-expanded','false');
    link.addEventListener('click',event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (!dialog.open) {
        dialog.showModal();
        dialog.scrollTop = 0;
        reflectDialogState();
      }
      if (!result.hidden) revealNotice(result);
    });
  });
  dialog.querySelector('.appointment-dialog-close').addEventListener('click',() => dialog.close());
  dialog.addEventListener('close',reflectDialogState);
  dialog.addEventListener('click',event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  result.querySelector('a').addEventListener('click',() => dialog.close());
  document.getElementById('copyStudioAddress').addEventListener('click', async () => {
    const address = document.getElementById('addressCopyFallback');
    const feedback = document.getElementById('addressCopyStatus');
    try {
      await navigator.clipboard.writeText(address.value);
      feedback.textContent = '门店地址已复制。';
    } catch {
      address.hidden = false;
      address.focus();
      address.select();
      feedback.textContent = '浏览器未允许自动复制，地址已选中。';
    }
  });
  document.querySelectorAll('.step-dot').forEach(dot => dot.setAttribute('aria-pressed',String(dot.classList.contains('active'))));
  if (!['https:','http:'].includes(location.protocol) || !crypto.randomUUID) {
    status.textContent = '请在 HTTPS 线上官网中提交预约申请。';
    return;
  }
  submit.disabled = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    const body = new URLSearchParams(new FormData(form));
    const payload = body.toString();
    if (payload !== previousPayload || !requestKey) {
      requestKey = crypto.randomUUID();
      previousPayload = payload;
    }
    body.set('idempotency_key', requestKey);
    busy = true;
    controls.forEach(control => { control.disabled = true; });
    submit.textContent = '正在提交…';
    status.removeAttribute('data-error');
    status.textContent = '正在发送申请，请稍候。';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(form.action, {method:'POST',body,signal:controller.signal,credentials:'omit',headers:{'Accept':'application/json'}});
      let data;
      try { data = await response.json(); } catch { throw new Error('服务暂时不可用，请保留填写内容并重试。'); }
      if (!response.ok) throw new Error(data.message || '暂时无法提交，请稍后重试。');
      if (!/^DEMO-[A-F0-9]{16}$/.test(data.reference || '')) throw new Error('未能确认提交结果，请重试，系统会自动防重复。');
      document.getElementById('appointmentReference').textContent = `申请编号：${data.reference}`;
      form.hidden = true;
      form.reset();
      result.hidden = false;
      revealNotice(result);
    } catch (error) {
      status.setAttribute('data-error','');
      status.textContent = error.name === 'AbortError' || error instanceof TypeError
        ? '网络暂时不可用，申请可能已送达。请保留填写内容并点击重试，系统会自动防重复。'
        : error.message;
      revealNotice(status);
    } finally {
      clearTimeout(timer);
      busy = false;
      controls.forEach(control => { control.disabled = false; });
      submit.textContent = '提交预约申请';
    }
  });
})();
