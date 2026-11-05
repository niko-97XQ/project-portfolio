import * as THREE from 'three';
import { createCameraCarousel } from './camera-carousel.js';
import { GLTFLoader } from './vendor/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from './vendor/addons/environments/RoomEnvironment.js';

const cameras=[
 ['hasselblad-500cm','Hasselblad 500C/M','MEDIUM FORMAT / 6 × 6','A12 · Planar 80mm · Waist-level finder'],
 ['nikon-f3','Nikon F3 HP','35 MM / SLR','DE-3 high-eyepoint finder · Nikkor 50mm'],
 ['leica-m6','Leica M6','35 MM / RANGEFINDER','Rangefinder · Manual focus'],
 ['polaroid-sx70','Polaroid SX-70','INSTANT / FOLDING SLR','Folding body · Integral film'],
 ['kodak-funsaver','Kodak FunSaver','35 MM / SINGLE USE','Fixed-focus lens · Built-in flash'],
 ['instax-mini90','Instax Mini 90','INSTANT / MINI','Retractable lens · Instant prints']
];
const films = [
 ["gold-200","Kodak Gold 200","35 MM / COLOR NEGATIVE","Gold 200 cassette · 36 exposures · C-41","film","gold"],
 ["portra-400","Kodak Portra 400","35 MM / COLOR NEGATIVE","Black-label Portra cassette · 36 exposures · C-41","film","portra"],
 ["ektar-100","Kodak Ektar 100","35 MM / COLOR NEGATIVE","Fine-grain color film · 36 exposures · C-41","film","ektar"],
 ["hp5-plus","Ilford HP5 Plus","35 MM / BLACK & WHITE","ISO 400 · White / green label · DX cassette","film","hp5"],
 ["portra-120","Portra 400 · 120","120 / COLOR NEGATIVE","Paper-backed roll · Flanged spool · C-41","film","portra"],
 ["polaroid-sx70-film","Polaroid SX-70 Film","INSTANT / SX-70","8 photos · Foil pouch · Square image","film","sx70"],
 ["instax-mini-film","Instax Mini Film","INSTANT / MINI","10 sheets · 54 × 86 mm print","film","instax"]
];
export function initCameraRoom(toast, studioTools){
 const $=id=>document.getElementById(id),canvas=$('camera-canvas'),cache=new Map();
 let renderer,scene,camera,pivot,model,request=0,drag=null;
 let carousel, cameraTrack, filmTrack;
 let choosingFilm = false, selectedFilm = 0;
 function loadAsset(item) {
  const path = './assets/' + (item[4] || 'cameras') + '/' + item[0] + '.glb';
  if (!cache.has(path)) {
   cache.set(path, new GLTFLoader().loadAsync(path).catch(error => {
    cache.delete(path);
    throw error;
   }));
  }
  return cache.get(path);
 }
 const studio = $('studio');
 let selectedCamera = 0;
 const picking = () => studio.classList.contains('is-selecting');
 function mode(photo) {
  $('camera-stage').hidden = photo;
  $('photo-stage').hidden = !photo;
  $('photo-return').hidden = photo || picking();
  if (!photo) resize();
 }
 $('photo-tab').onclick = () => mode(true);
 $('import-button').addEventListener('click',()=>mode(true));
 $('photo-input').addEventListener('change',()=>mode(true));
 $('studio').addEventListener('drop',e=>{if(e.dataTransfer?.files?.length)mode(true);});
 $('look-list').addEventListener('click',()=>mode(true));
 $('adjustments').addEventListener('input',()=>mode(true));
 $('print-template').addEventListener('change',()=>mode(true));
 $('print-options').addEventListener('input',()=>mode(true));
 $('compare-button').addEventListener('click',()=>mode(true));
 function setup(){
  if(renderer)return;
  renderer=new THREE.WebGLRenderer({canvas,antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,1.75));renderer.setClearColor(0x000000,0);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.95;
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(35,1,.01,100);camera.position.set(3.8,2.1,6.7);camera.lookAt(0,0,0);
  const pmrem=new THREE.PMREMGenerator(renderer),room=new RoomEnvironment();scene.environment=pmrem.fromScene(room,.04).texture;room.dispose();pmrem.dispose();scene.environmentIntensity=.65;
  const key=new THREE.DirectionalLight(0xfff7e8,1.8);key.position.set(3,5,5);scene.add(key);const rim=new THREE.DirectionalLight(0xe2e8ef,.85);rim.position.set(-4,2,-3);scene.add(rim);scene.add(new THREE.HemisphereLight(0xffffff,0x383734,.35));
  pivot=new THREE.Group();scene.add(pivot);
  camera.updateMatrixWorld();
  const draw = () => renderer.render(scene,camera);
  cameraTrack = createCameraCarousel(scene, camera, cameras, loadAsset, draw);
  filmTrack = createCameraCarousel(scene, camera, films, loadAsset, draw);
  carousel = choosingFilm ? filmTrack : cameraTrack;
 }
 function resize(){if(!renderer||$('studio').hidden||$('camera-stage').hidden)return;const r=canvas.getBoundingClientRect();if(!r.width||!r.height)return;renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();if(picking())carousel.layout();renderer.render(scene,camera);}
 function reset(){if(!pivot)return;pivot.rotation.set(0,model?.userData.isFilm ? .45 : -.15,0);camera.zoom=1;camera.updateProjectionMatrix();resize();}
 async function choose(item, direction = 0){
  const index = cameras.indexOf(item);
  if (index >= 0) selectedCamera = index;
  const token=++request;mode(false);$('camera-name').textContent=item[1];$('camera-format').textContent=item[2];$('camera-detail').textContent=item[3];$('camera-status').textContent='Loading model…';
  document.querySelectorAll('.camera-choice').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.camera===item[0])));
  $('camera-fallback').src=`./assets/${item[4]||'cameras'}/${item[0]}.png`;
  try{setup();if(model){pivot.remove(model);model=null;}resize();
   if (picking()) {
    camera.zoom = 1;
    camera.updateProjectionMatrix();
    await carousel.show(choosingFilm ? selectedFilm : index, direction);
    if (token === request) $('camera-fallback').hidden = true;
    return;
   }
   carousel.hide();
   const asset = await loadAsset(item);
   if(token!==request)return;model=asset.scene;model.userData.isFilm=item[4]==='film';model.scale.setScalar(1);model.position.set(0,0,0);model.updateMatrixWorld(true);const box=new THREE.Box3().setFromObject(model),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3()),scale=3.45/Math.max(size.x,size.y,size.z);model.scale.setScalar(scale);model.position.copy(center.multiplyScalar(-scale));pivot.add(model);reset();$('camera-status').textContent='Drag to rotate · Scroll to zoom';$('camera-fallback').hidden=true;
  }catch{if(token!==request)return;$('camera-fallback').hidden=false;$('camera-status').textContent='Model preview unavailable. Showing the studio render.';toast('The interactive model could not load. Its render is still available.');}
 }
 function populate(items) {
  const isFilm = items === films;
  const selected = isFilm ? selectedFilm : selectedCamera;
  $('collection-count').textContent = String(items.length).padStart(2, '0');
  $('camera-list').replaceChildren();
  for (const [i, item] of items.entries()) {
   const button = document.createElement('button');
   button.className = 'camera-choice';
   button.setAttribute('aria-pressed', String(i === selected));
   const image = document.createElement('img');
   image.src = './assets/' + (item[4] || 'cameras') + '/' + item[0] + '.png';
   image.alt = '';
   const name = document.createElement('span');
   name.textContent = item[1];
   button.append(image, name);
   button.onclick = () => {
    if (isFilm) {
     selectedFilm = i;
     studioTools.applyLook(item[5]);
    } else {
     selectedCamera = i;
     $('current-camera').textContent = item[1];
    }
    $('gear-panel').hidePopover();
    $('gear-button').focus();
   };
   $('camera-list').append(button);
  }
 }
 const tabs = studio.querySelectorAll('[data-collection]');
 function collection(tab) {
  for (const button of tabs) button.setAttribute('aria-pressed', String(button === tab));
  populate(tab.dataset.collection === 'film' ? films : cameras);
 }
 tabs.forEach(tab => tab.onclick = () => collection(tab));
 $('gear-panel').addEventListener('beforetoggle', event => {
  if (event.newState === 'open') collection(studio.querySelector('[data-collection][aria-pressed="true"]'));
 });
 canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);});
 canvas.addEventListener('pointermove',e=>{const turn=picking()?carousel?.turntable(choosingFilm ? selectedFilm : selectedCamera):pivot;if(!drag||!turn)return;turn.rotation.y+=(e.clientX-drag.x)*.008;turn.rotation.x=THREE.MathUtils.clamp(turn.rotation.x+(e.clientY-drag.y)*.006,-1.2,1.2);drag={x:e.clientX,y:e.clientY};resize();});
 canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);
 canvas.addEventListener('wheel',e=>{if(picking()||!camera)return;e.preventDefault();camera.zoom=THREE.MathUtils.clamp(camera.zoom*Math.exp(-e.deltaY*.001),.7,1.8);camera.updateProjectionMatrix();resize();},{passive:false});
 canvas.tabIndex=0;canvas.addEventListener('keydown',e=>{if(!pivot)return;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();if(e.key.includes('Left'))pivot.rotation.y-=.15;if(e.key.includes('Right'))pivot.rotation.y+=.15;if(e.key.includes('Up'))pivot.rotation.x-=.1;if(e.key.includes('Down'))pivot.rotation.x+=.1;resize();}});
 $('reset-camera').onclick=reset;
 new ResizeObserver(resize).observe($('camera-stage'));
 function setPickerStep(film) {
  carousel?.hide();
  choosingFilm = film;
  carousel = film ? filmTrack : cameraTrack;
  studio.classList.toggle('choosing-film', film);
  $('previous-camera').setAttribute('aria-label', film ? 'Previous film' : 'Previous camera');
  $('next-camera').setAttribute('aria-label', film ? 'Next film' : 'Next camera');
  $('use-camera').textContent = film ? 'Use this look' : 'Use this camera';
 }
 function openPicker() {
  setPickerStep(false);
  studio.classList.add('is-selecting');
  studioTools.setFinishing(false);
  choose(cameras[selectedCamera]);
  $('use-camera').focus({preventScroll:true});
 }
 function step(direction) {
  if (choosingFilm) {
   selectedFilm = (selectedFilm + direction + films.length) % films.length;
   choose(films[selectedFilm], direction);
   return;
  }
  const next = (selectedCamera + direction + cameras.length) % cameras.length;
  choose(cameras[next], direction);
 }
 $('previous-camera').onclick = () => step(-1);
 $('next-camera').onclick = () => step(1);
 $('back-to-cameras').onclick = openPicker;
 $('use-camera').onclick = () => {
  if (!choosingFilm) {
   setPickerStep(true);
   lastStep = performance.now();
   choose(films[selectedFilm]);
   return;
  }
  const film = films[selectedFilm];
  setPickerStep(false);
  studio.classList.remove('is-selecting');
  request++;
  $('current-camera').textContent = cameras[selectedCamera][1];
  studioTools.applyLook(film[5]);
  mode(true);
  $('import-button').focus({preventScroll:true});
 };
 let lastStep = -Infinity, accumulated = 0, lastWheel = -Infinity;
 studio.addEventListener('wheel', event => {
  if (!picking() || event.ctrlKey) return;
  event.preventDefault();
  const now = performance.now();
  if (now - lastWheel > 160) accumulated = 0;
  lastWheel = now;
  if (now - lastStep < 450) return;
  const delta = Math.abs(event.deltaY) >= Math.abs(event.deltaX) ? event.deltaY : event.deltaX;
  accumulated += delta * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? innerHeight : 1);
  if (Math.abs(accumulated) >= 40) {
   step(Math.sign(accumulated));
   lastStep = now;
   accumulated = 0;
  }
 }, {passive:false});
 studio.addEventListener('keydown', event => {
  if (!picking()) return;
  const keys = {ArrowRight:1, ArrowDown:1, ArrowLeft:-1, ArrowUp:-1};
  if (keys[event.key]) {
   event.preventDefault();
   event.stopPropagation();
   if (!event.repeat) step(keys[event.key]);
  }
 }, true);
 let touchOrigin;
 studio.addEventListener('touchstart', event => {
  touchOrigin = picking() && event.touches.length === 1 ? event.touches[0] : null;
 }, {passive:true});
 studio.addEventListener('touchend', event => {
  if (!touchOrigin || !picking()) return;
  const dx = event.changedTouches[0].clientX - touchOrigin.clientX;
  const dy = event.changedTouches[0].clientY - touchOrigin.clientY;
  const distance = Math.abs(dx) > Math.abs(dy) ? dx : dy;
  if (Math.abs(distance) > 45) step(-Math.sign(distance));
  touchOrigin = null;
 }, {passive:true});
 studio.addEventListener('touchcancel', () => {touchOrigin = null;});
 for (const type of ['dragenter','dragover','drop']) {
  studio.addEventListener(type, event => {
   if (picking()) {event.preventDefault(); event.stopImmediatePropagation();}
  }, true);
 }
 return {openPicker};
}




