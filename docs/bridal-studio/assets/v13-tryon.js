(() => {
  'use strict';
  const video = document.getElementById('cameraPreview');
  const empty = document.getElementById('cameraEmpty');
  const caption = document.getElementById('cameraCaption');
  const start = document.getElementById('startCamera');
  const stop = document.getElementById('stopCamera');
  const mirror = document.getElementById('mirrorCamera');
  const status = document.getElementById('cameraStatus');
  const choices = [...document.querySelectorAll('[data-garment]')];
  let stream = null;
  let requestVersion = 0;
  let starting = false;
  let mirrored = true;

  function setStatus(message, error = false) {
    status.textContent = message;
    status.toggleAttribute('data-error', error);
  }

  function stopCamera(message = '摄像头已关闭') {
    requestVersion += 1;
    starting = false;
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.pause();
    video.srcObject = null;
    video.hidden = true;
    empty.hidden = false;
    caption.hidden = true;
    start.disabled = false;
    start.textContent = '开启摄像头';
    stop.disabled = true;
    mirror.disabled = true;
    setStatus(message);
  }

  const messages = {
    NotAllowedError: '未获摄像头权限。请在浏览器的站点权限中允许后重试。',
    PermissionDeniedError: '未获摄像头权限。请在浏览器的站点权限中允许后重试。',
    NotFoundError: '未找到摄像头，请连接摄像头后重试。',
    DevicesNotFoundError: '未找到摄像头，请连接摄像头后重试。',
    NotReadableError: '摄像头可能正被其他程序使用，请关闭占用程序后重试。',
    TrackStartError: '摄像头可能正被其他程序使用，请关闭占用程序后重试。',
    OverconstrainedError: '当前摄像头不支持所需模式，请更换设备后重试。',
    AbortError: '摄像头启动已中断，请重试。',
  };

  start.addEventListener('click', async () => {
    if (starting || stream) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setStatus('请通过 HTTPS 官网或 localhost 打开此页面，并使用支持摄像头的浏览器。', true);
      return;
    }
    const version = ++requestVersion;
    starting = true;
    start.disabled = true;
    start.textContent = '等待摄像头权限…';
    stop.disabled = false;
    setStatus('等待浏览器授权。不会启用麦克风。');
    let candidate = null;
    try {
      candidate = await navigator.mediaDevices.getUserMedia({
        video: {facingMode: 'user', width: {ideal: 1280}, height: {ideal: 720}},
        audio: false,
      });
      // A late permission response must not reopen a stopped or background session.
      if (version !== requestVersion || document.hidden) {
        candidate.getTracks().forEach(track => track.stop());
        return;
      }
      stream = candidate;
      candidate.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
        if (stream === candidate) stopCamera('摄像头连接已断开，请重新开启。');
      }, {once: true}));
      video.srcObject = candidate;
      video.hidden = false;
      empty.hidden = true;
      await video.play();
      if (version !== requestVersion) return;
      if (!candidate.getVideoTracks().some(track => track.readyState === 'live')) {
        throw new DOMException('Camera stopped during startup', 'NotReadableError');
      }
      starting = false;
      caption.hidden = false;
      stop.disabled = false;
      mirror.disabled = false;
      video.classList.toggle('is-mirrored', mirrored);
      setStatus('本地预览中 · 未接入 AI 换装');
    } catch (error) {
      if (candidate) candidate.getTracks().forEach(track => track.stop());
      if (version !== requestVersion) return;
      stopCamera();
      setStatus(messages[error.name] || '无法开启摄像头，请检查设备与浏览器权限后重试。', true);
    }
  });

  stop.addEventListener('click', () => stopCamera());
  mirror.addEventListener('click', () => {
    mirrored = !mirrored;
    mirror.setAttribute('aria-pressed', String(mirrored));
    video.classList.toggle('is-mirrored', mirrored);
  });

  choices.forEach(choice => choice.addEventListener('click', () => {
    choices.forEach(button => button.setAttribute('aria-pressed', String(button === choice)));
    const image = document.getElementById('selectedGarmentImage');
    image.src = choice.querySelector('img').src;
    image.alt = `${choice.dataset.brand} ${choice.dataset.name} 婚纱参考图`;
    document.getElementById('selectedGarmentName').textContent = choice.dataset.name;
    document.getElementById('selectedGarmentBrand').textContent = choice.dataset.brand;
    document.getElementById('selectedGarmentDetail').textContent = choice.dataset.detail;
  }));

  document.addEventListener('visibilitychange', () => {
    if (document.hidden && (stream || starting)) stopCamera('页面已转入后台，摄像头已关闭。');
  });
  window.addEventListener('pagehide', () => stopCamera());
})();
