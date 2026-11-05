import { initCameraRoom } from './camera-room.js';
import { photos } from './photos.js';
import { initStudio } from './studio.js';
const $=id=>document.getElementById(id);
let scene,opening=false,toastTimer;
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const studio=initStudio(showToast);
const cameraRoom = initCameraRoom(showToast, studio);
function showToast(message){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.add('is-visible');toastTimer=setTimeout(()=>$('toast').classList.remove('is-visible'),4000)}
async function enterStudio(){
  if(opening||!$('studio').hidden)return;
  opening=true;$('darkroom-switch').disabled=true;$('cover').setAttribute('aria-busy','true');document.body.classList.add('is-opening');scene?.enter();
  await new Promise(resolve=>setTimeout(resolve,reduced.matches?180:2500));
  $('cover').hidden=true;$('cover').inert=true;$('studio').hidden=false;$('studio').inert=false;
  document.body.classList.remove('is-opening');scene?.setActive(false);opening=false;$('darkroom-switch').disabled=false;$('cover').removeAttribute('aria-busy');
  cameraRoom.openPicker();
}
$('darkroom-switch').addEventListener('click',enterStudio);
$('back-cover').addEventListener('click',()=>{$('studio').hidden=true;$('studio').inert=true;$('cover').hidden=false;$('cover').inert=false;scene?.reset();scene?.setActive(true);$('cover').focus({preventScroll:true})});
let paused=reduced.matches;
function setPaused(value){paused=value;scene?.pause(value);$('motion-button').setAttribute('aria-pressed',String(value));$('motion-button').setAttribute('aria-label',value?'Resume film movement':'Pause film movement');$('motion-symbol').setAttribute('d',value?'m6 4 7 5-7 5z':'M6 5v8M12 5v8')}
$('motion-button').addEventListener('click',()=>setPaused(!paused));reduced.addEventListener('change',e=>setPaused(e.matches));setPaused(paused);
const credits=$('credits-dialog');$('credits-button').addEventListener('click',()=>{credits.showModal();scene?.setActive(false)});credits.querySelector('.dialog-close').addEventListener('click',()=>credits.close());credits.addEventListener('close',()=>scene?.setActive($('studio').hidden));
credits.addEventListener('click',e=>{const r=credits.getBoundingClientRect();if(e.target===credits&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom))credits.close()});
for(const photo of photos){const li=document.createElement('li'),a=document.createElement('a'),small=document.createElement('small');a.href=photo.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=photo.author;small.textContent=photo.title+' ↗';a.append(small);li.append(a);$('photo-credits').append(li)}
import('./cover.js').then(m=>m.createCover($('cover-canvas'),photos)).then(result=>{scene=result;scene.pause(paused);scene.setActive($('studio').hidden);document.body.classList.add('is-ready');$('loading').setAttribute('aria-hidden','true')}).catch(error=>{document.body.classList.add('is-fallback');$('loading').hidden=true;console.info('3D cover unavailable; accessible studio entry remains available.',error.message)});

