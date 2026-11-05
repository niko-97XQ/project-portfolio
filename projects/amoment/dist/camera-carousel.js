import * as THREE from 'three';

// One scene and one renderer keep the incoming and outgoing cameras in the
// same light while they travel across the frame.
export function createCameraCarousel(scene, view, items, load, draw) {
  const track = new THREE.Group();
  scene.add(track);
  const slots = items.map(() => {
    const slot = new THREE.Group();
    track.add(slot);
    return slot;
  });
  const ready = new Map();
  const right = new THREE.Vector3().setFromMatrixColumn(view.matrix, 0).normalize();
  const towardsViewer = view.position.clone().normalize();
  const count = items.length;
  const wrap = value => ((value % count) + count) % count;
  let position = 0, target = 0, frame = 0, revision = 0;
  track.visible = false;

  function layout() {
    const width = 2 * view.position.length() * Math.tan(THREE.MathUtils.degToRad(view.fov / 2)) * view.aspect;
    const fit = Math.min(1, width * .54 / 3.45);
    slots.forEach((slot, index) => {
      const offset = wrap(index - position + count / 2) - count / 2;
      const distance = Math.min(1, Math.abs(offset));
      slot.visible = Math.abs(offset) < 1.85;
      slot.position.copy(right).multiplyScalar(width * .433 * offset);
      slot.position.addScaledVector(towardsViewer, -.35 * distance);
      slot.scale.setScalar(fit * (1 - .27 * distance));
      slot.rotation.y = -.15 - .22 * THREE.MathUtils.clamp(offset, -1, 1);
    });
  }
  function prepare(index) {
    if (!ready.has(index)) {
      const pending = load(items[index]).then(asset => {
        const instance = asset.scene.clone(true);
        instance.scale.setScalar(1);
        instance.position.set(0, 0, 0);
        instance.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(instance);
        const size = bounds.getSize(new THREE.Vector3());
        const scale = 3.45 / Math.max(size.x, size.y, size.z);
        instance.scale.setScalar(scale);
        instance.position.copy(bounds.getCenter(new THREE.Vector3()).multiplyScalar(-scale));
        const turntable = new THREE.Group();
        turntable.add(instance);
        slots[index].add(turntable);
        if (track.visible) { layout(); draw(); }
      }).catch(error => { ready.delete(index); throw error; });
      ready.set(index, pending);
    }
    return ready.get(index);
  }
  async function show(index, direction) {
    const token = ++revision;
    cancelAnimationFrame(frame);
    track.visible = true;
    if (!direction) position = target = index;
    else target += direction;
    layout();
    draw();
    const destination = prepare(index);
    for (const neighbor of [wrap(index - 1), wrap(index + 1)]) {
      prepare(neighbor).catch(() => {});
    }
    await destination;
    if (revision !== token) return;
    const start = performance.now(), from = position, to = target;
    const duration = direction && !matchMedia('(prefers-reduced-motion: reduce)').matches ? 540 : 0;
    function move(now) {
      const t = duration ? Math.min(1, (now - start) / duration) : 1;
      position = from + (to - from) * (t * t * (3 - 2 * t));
      layout();
      draw();
      if (t < 1) frame = requestAnimationFrame(move);
    }
    move(start);
  }
  return {
    show, layout,
    turntable: index => slots[index].children[0],
    hide() { revision++; cancelAnimationFrame(frame); track.visible = false; }
  };
}
