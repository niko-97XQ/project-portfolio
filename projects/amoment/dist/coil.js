import * as THREE from './vendor/three.module.js';

export async function createCoil(canvas, photos) {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.setClearColor(0x090c0b, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.25;
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x090c0b, .024);
  const camera = new THREE.PerspectiveCamera(47, 1, .1, 90);
  camera.position.set(0, 3, 21);
  camera.lookAt(0, 0, 0);
  const group = new THREE.Group();
  scene.add(group);
  scene.add(new THREE.AmbientLight(0xa5b49a, 1.35));
  const key = new THREE.DirectionalLight(0xe4e0b9, 3.2);
  key.position.set(3, 12, 10); scene.add(key);
  const rim = new THREE.DirectionalLight(0x9da98e, 2.4);
  rim.position.set(-9, 5, -8); scene.add(rim);
  const warm = new THREE.PointLight(0xc48c48, 60, 40, 2);
  warm.position.set(-2, 3, 8); scene.add(warm);

  const textureCanvas = document.createElement('canvas');
  textureCanvas.width = 2048; textureCanvas.height = 320;
  const ctx = textureCanvas.getContext('2d');
  ctx.fillStyle = '#32382a'; ctx.fillRect(0, 0, 2048, 320);
  const loaded = await Promise.all(photos.slice(0, 4).map(photo => new Promise(resolve => {
    const img = new Image(); img.onload = () => resolve(img); img.onerror = () => resolve(null); img.src = `./assets/photos/${photo.file}`;
  })));
  for (let i = 0; i < 4; i++) {
    const x = i * 512;
    ctx.fillStyle = '#192217'; ctx.fillRect(x + 18, 41, 474, 238);
    if (loaded[i]) {
      ctx.globalAlpha = .26;
      const img = loaded[i]; const ratio = Math.max(474 / img.width, 238 / img.height);
      ctx.save(); ctx.beginPath(); ctx.rect(x + 18, 41, 474, 238); ctx.clip();
      ctx.drawImage(img, x + 18 + (474 - img.width * ratio) / 2, 41 + (238 - img.height * ratio) / 2, img.width * ratio, img.height * ratio);
      ctx.restore(); ctx.globalAlpha = 1;
    }
    ctx.fillStyle = '#9d9d64'; ctx.font = '9px monospace';
    ctx.fillText('AMOMENT  ·  400', x + 20, 35); ctx.fillText(`${String(i + 1).padStart(2, '0')}  ▷`, x + 442, 293);
  }
  for (let x = 10; x < 2048; x += 44) {
    ctx.clearRect(x, 8, 20, 15); ctx.clearRect(x, 299, 20, 15);
  }
  const texture = new THREE.CanvasTexture(textureCanvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.repeat.x = 3.3;
  texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());

  const endAngle = Math.PI * 2.5;
  const endRadius = 1.5 + .18 * (endAngle + Math.PI);
  const leader = new THREE.CubicBezierCurve3(new THREE.Vector3(0, 0, endRadius), new THREE.Vector3(-7, -.3, endRadius + .5), new THREE.Vector3(-12, -5.5, -4), new THREE.Vector3(-25, -4, -5));
  const center = t => {
    if (t <= .65) {
      const angle = -Math.PI + (t / .65) * Math.PI * 3.5;
      const radius = 1.5 + .18 * (angle + Math.PI);
      return new THREE.Vector3(Math.cos(angle) * radius, 0, Math.sin(angle) * radius);
    }
    return leader.getPoint((t - .65) / .35);
  };
  const positions = [], uvs = [], indices = [], edges = [[], []];
  const segments = 640, across = 4;
  for (let i = 0; i <= segments; i++) {
    const t = i / segments, p = center(t);
    const twist = t > .65 ? Math.sin((t - .65) / .35 * Math.PI) * .48 : .035 * Math.sin(t * 10);
    for (let j = 0; j <= across; j++) {
      const v = j / across, distance = (v - .5) * 4.4;
      const point = new THREE.Vector3(p.x + Math.sin(twist) * distance * .5, p.y + Math.cos(twist) * distance, p.z + Math.sin(twist) * distance);
      positions.push(point.x, point.y, point.z); uvs.push(t, v);
      if (j === 0) edges[0].push(point); if (j === across) edges[1].push(point);
      if (i < segments && j < across) {
        const a = i * (across + 1) + j, b = a + across + 1;
        indices.push(a, b, a + 1, b, b + 1, a + 1);
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices); geometry.computeVertexNormals();
  const material = new THREE.MeshStandardMaterial({ map: texture, side: THREE.DoubleSide, roughness: .42, metalness: .38, alphaTest: .5, color: 0xb5b8a3 });
  group.add(new THREE.Mesh(geometry, material));
  for (const points of edges) group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color: 0x8d977a, transparent: true, opacity: .23 })));
  let width = 0, height = 0, mobile = false;
  function resize() {
    const bounds = canvas.getBoundingClientRect();
    if (bounds.width === width && bounds.height === height) return;
    width = bounds.width; height = bounds.height; mobile = width < 760;
    renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
    group.position.set(mobile ? 4.3 : 6.2, mobile ? 2.9 : 2.3, mobile ? -3 : -1);
    group.scale.setScalar(mobile ? .88 : 1.14);
  }
  resize();
  let lastDraw = -100;
  return {
    draw(time, pointer, moving, reduced) {
      resize();
      if (time - lastDraw < 32) return;
      lastDraw = time;
      const drift = reduced ? 0 : Math.sin(time * .00012) * .025;
      group.rotation.set(.48 + (reduced ? 0 : pointer.y * .025), -.24 + drift + (reduced ? 0 : pointer.x * .025), -.22);
      if (moving && !reduced) texture.offset.x = (texture.offset.x + .00012) % 1;
      renderer.render(scene, camera);
    },
    dispose() { geometry.dispose(); material.dispose(); texture.dispose(); renderer.dispose(); }
  };
}
