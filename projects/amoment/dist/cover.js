import * as THREE from 'three';
import { GLTFLoader } from './vendor/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from './vendor/addons/environments/RoomEnvironment.js';
import { mergeGeometries } from './vendor/addons/utils/BufferGeometryUtils.js';

const clamp=THREE.MathUtils.clamp;
const wrap=n=>(n%1+1)%1;
const reduced=matchMedia('(prefers-reduced-motion: reduce)');

function batchOpaque(root){
  root.updateMatrixWorld(true);
  const inverse=root.matrixWorld.clone().invert(),groups=new Map(),remove=[];
  root.traverse(obj=>{
    if(!obj.isMesh||Array.isArray(obj.material)||obj.material.transmission>0||!obj.visible)return;
    const key=obj.material.uuid;
    if(!groups.has(key))groups.set(key,{material:obj.material,geometry:[]});
    const g=obj.geometry.clone().applyMatrix4(new THREE.Matrix4().multiplyMatrices(inverse,obj.matrixWorld));
    // Consistent attribute sets make bevelled solids and converted lettering merge safely.
    for(const attr of Object.keys(g.attributes))if(!['position','normal','uv'].includes(attr))g.deleteAttribute(attr);
    if(!g.attributes.uv)g.setAttribute('uv',new THREE.Float32BufferAttribute(new Float32Array(g.attributes.position.count*2),2));
    groups.get(key).geometry.push(g);remove.push(obj);
  });
  for(const obj of remove)obj.parent.remove(obj);
  for(const {material,geometry}of groups.values()){
    const merged=mergeGeometries(geometry.map(g=>g.index?g.toNonIndexed():g),false);
    if(!merged)continue;
    const mesh=new THREE.Mesh(merged,material);mesh.name=material.name;mesh.castShadow=true;mesh.receiveShadow=true;root.add(mesh);
    geometry.forEach(g=>g.dispose());
  }
}

async function makeAtlas(photos){
  const images=await Promise.all(photos.map(p=>new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=reject;image.src=`./assets/photos/${p.file}`})));
  const order=[4,0,1,2,3,5],width=5400,height=660,tile=900;
  const sharp=document.createElement('canvas');sharp.width=width;sharp.height=height;
  const ctx=sharp.getContext('2d');ctx.fillStyle='#100e0c';ctx.fillRect(0,0,width,height);
  order.forEach((index,i)=>{
    const x=i*tile,img=images[index];
    const rect={x:x+20,y:96,w:860,h:468};
    const scale=Math.max(rect.w/img.width,rect.h/img.height);
    ctx.save();ctx.beginPath();ctx.roundRect(rect.x,rect.y,rect.w,rect.h,3);ctx.clip();
    ctx.drawImage(img,rect.x+(rect.w-img.width*scale)/2,rect.y+(rect.h-img.height*scale)/2,img.width*scale,img.height*scale);ctx.restore();
    ctx.fillStyle='#e4b45e';ctx.font='600 32px monospace';ctx.fillText('AMOMENT  400',x+24,31);ctx.fillText('35 MM',x+740,31);
    ctx.fillText(String(i+1).padStart(2,'0')+' A',x+24,654);ctx.fillText('▸',x+440,654);ctx.fillText(String(i+1).padStart(2,'0'),x+820,654);
  });
  const soft=document.createElement('canvas');soft.width=width;soft.height=height;
  const s=soft.getContext('2d');s.drawImage(sharp,0,0);
  // Defocus only the photographs. Rebates, frame labels and perforations stay crisp.
  order.forEach((index,i)=>{
    const x=i*tile+20,img=images[index],y=96,w=860,h=468;
    const scale=Math.max(w/img.width,h/img.height);
    s.save();s.beginPath();s.roundRect(x,y,w,h,3);s.clip();
    s.filter='blur(.65px) saturate(.96)';
    s.drawImage(img,x+(w-img.width*scale)/2,y+(h-img.height*scale)/2,img.width*scale,img.height*scale);s.restore();
  });
  for(const context of [ctx,s]){
    context.fillStyle='#dcd8cc';
    for(let i=0;i<48;i++)for(const y of [43,578]){
      context.beginPath();context.roundRect((i+.5)*112.5,y,56.25,40,7);context.fill();
    }
  }
  return{sharp,soft};
}

export async function createCover(canvas,photos){
  const renderer=new THREE.WebGLRenderer({canvas,alpha:false,antialias:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));renderer.setClearColor(0x000000,1);
  renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.92;
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFShadowMap;
  const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-10,10,6,-6,.1,90);
  const pmrem=new THREE.PMREMGenerator(renderer),room=new RoomEnvironment();
  scene.environment=pmrem.fromScene(room,.05).texture;scene.environmentIntensity=.55;room.dispose();pmrem.dispose();
  const key=new THREE.DirectionalLight(0xf5f5ed,2.8);key.position.set(-5,12,7);key.castShadow=true;key.shadow.mapSize.set(2048,2048);key.shadow.camera.left=-13;key.shadow.camera.right=13;key.shadow.camera.top=10;key.shadow.camera.bottom=-10;key.shadow.camera.far=40;key.shadow.bias=-.0002;key.shadow.normalBias=.015;key.shadow.radius=4;scene.add(key);
  const rim=new THREE.DirectionalLight(0xe2e5f3,2.6);rim.position.set(3,8,-9);scene.add(rim);
  const warm=new THREE.DirectionalLight(0xffd0a0,1.25);warm.position.set(-9,3,-2);scene.add(warm);
  scene.add(new THREE.HemisphereLight(0xe3edf5,0x15120f,.4));
  const group=new THREE.Group();scene.add(group);
  const loader=new GLTFLoader();
  const [rigAsset,loupeAsset,atlas]=await Promise.all([loader.loadAsync('./assets/models/film-rig.glb'),loader.loadAsync('./assets/models/reference-loupe.glb'),makeAtlas(photos)]);
  const rig=rigAsset.scene,loupe=loupeAsset.scene;
  const strip=rig.getObjectByName('FILM_STRIP'),lever=rig.getObjectByName('REWIND_LEVER'),base=rig.getObjectByName('REWIND_BASE');
  if(!strip)throw new Error('Film model is incomplete');
  group.add(rig,loupe);
  const filmPose=new THREE.Group();rig.add(filmPose);filmPose.attach(strip);
  filmPose.scale.setScalar(1.18);filmPose.position.z=.65;
  // Account for the camera's elevation so the projected strip rises exactly 10 degrees.
  filmPose.rotation.y=Math.atan(Math.tan(THREE.MathUtils.degToRad(8))/Math.cos(Math.atan(5/20)));
  const filmLength=36,filmHeight=3.3,frameWidth=4.5;
  const texture=new THREE.CanvasTexture(atlas.soft);texture.flipY=false;texture.colorSpace=THREE.SRGBColorSpace;texture.wrapS=THREE.RepeatWrapping;texture.repeat.x=filmLength/(frameWidth*6);texture.offset.x=-.0556;texture.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
  const filmMaterial=new THREE.MeshBasicMaterial({map:texture,side:THREE.DoubleSide,toneMapped:false});
  const filmViewport={value:new THREE.Vector2(1,1)},darkroomLevel={value:0};
  filmMaterial.onBeforeCompile=shader=>{
    shader.uniforms.uFilmViewport=filmViewport;shader.uniforms.uDarkroom=darkroomLevel;
    shader.fragmentShader='uniform vec2 uFilmViewport; uniform float uDarkroom;\n'+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',`
      float edgeDistance=abs(gl_FragCoord.x/uFilmViewport.x*2.0-1.0);
      float edgeSoftness=smoothstep(0.58,1.0,edgeDistance);
      vec2 delta=vec2(1.0/5400.0,1.0/660.0)*10.0*edgeSoftness*edgeSoftness;
      vec4 filmColor=texture2D(map,vMapUv)*0.28;
      filmColor+=texture2D(map,vMapUv+vec2(delta.x,0.0))*0.12;
      filmColor+=texture2D(map,vMapUv-vec2(delta.x,0.0))*0.12;
      filmColor+=texture2D(map,vMapUv+vec2(0.0,delta.y))*0.12;
      filmColor+=texture2D(map,vMapUv-vec2(0.0,delta.y))*0.12;
      filmColor+=texture2D(map,vMapUv+delta)*0.055;
      filmColor+=texture2D(map,vMapUv-delta)*0.055;
      filmColor+=texture2D(map,vMapUv+vec2(delta.x,-delta.y))*0.055;
      filmColor+=texture2D(map,vMapUv+vec2(-delta.x,delta.y))*0.055;
      filmColor.rgb*=1.0-smoothstep(0.68,1.0,edgeDistance);
      float luminance=dot(filmColor.rgb,vec3(0.2126,0.7152,0.0722));
      filmColor.rgb=mix(filmColor.rgb,vec3(luminance*.80,luminance*.025,luminance*.008),uDarkroom*.95);
      diffuseColor*=filmColor;
    `);
  };
  // A single moving emulsion surface keeps holes, borders, labels and photos locked.
  strip.traverse(o=>{if(o.isMesh)o.visible=false});
  const filmGeometry=new THREE.PlaneGeometry(filmLength,filmHeight);
  const filmUV=filmGeometry.attributes.uv;
  for(let i=0;i<filmUV.count;i++)filmUV.setY(i,1-filmUV.getY(i));
  const filmSurface=new THREE.Mesh(filmGeometry,filmMaterial);
  filmSurface.name='Synchronized film and perforations';filmSurface.rotation.x=-Math.PI/2;filmSurface.position.y=.27;
  filmSurface.layers.enable(1);strip.add(filmSurface);
  const sharpTexture=texture.clone();sharpTexture.image=atlas.sharp;sharpTexture.needsUpdate=true;
  for(const part of [base,lever])part?.removeFromParent();
  loupe.traverse(o=>{
    if(!o.isMesh)return;
    o.castShadow=true;o.receiveShadow=true;
    if(/OPTICAL_LENS/.test(o.name))o.visible=false;
    if(/White enamel/.test(o.material.name))o.material.side=THREE.DoubleSide;
    if(o.material.transmission>0){o.material.transmission=.98;o.material.roughness=.045;o.material.thickness=.08;o.material.envMapIntensity=.7;o.castShadow=false}
  });
  batchOpaque(loupe);
  // Capture the sharp moving film through the same camera, preserving perspective.
  const opticalTarget=new THREE.WebGLRenderTarget(1,1,{depthBuffer:false});
  opticalTarget.texture.colorSpace=THREE.SRGBColorSpace;
  const opticalMaterial=new THREE.ShaderMaterial({
    name:'Radial optical glass image',
    toneMapped:false,
    uniforms:{
      uImage:{value:opticalTarget.texture},
      uTexel:{value:new THREE.Vector2(1,1)},
      uLensCenter:{value:new THREE.Vector2(.5,.5)}
    },
    vertexShader:`
      varying vec2 vUv;
      varying vec4 vClip;
      void main(){
        vUv=uv;
        gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);
        vClip=gl_Position;
      }
    `,
    fragmentShader:`
      uniform sampler2D uImage;
      uniform vec2 uTexel;
      uniform vec2 uLensCenter;
      varying vec2 vUv;
      varying vec4 vClip;

      vec3 soften(vec2 uv,vec2 stepSize){
        // A normalized, circular 13-tap kernel avoids a square blur at the rim.
        vec3 c=texture2D(uImage,uv).rgb*0.20;
        c+=texture2D(uImage,uv+stepSize*vec2(0.45,0.0)).rgb*0.12;
        c+=texture2D(uImage,uv-stepSize*vec2(0.45,0.0)).rgb*0.12;
        c+=texture2D(uImage,uv+stepSize*vec2(0.0,0.45)).rgb*0.12;
        c+=texture2D(uImage,uv-stepSize*vec2(0.0,0.45)).rgb*0.12;
        c+=texture2D(uImage,uv+stepSize*vec2(1.0,0.0)).rgb*0.04;
        c+=texture2D(uImage,uv-stepSize*vec2(1.0,0.0)).rgb*0.04;
        c+=texture2D(uImage,uv+stepSize*vec2(0.0,1.0)).rgb*0.04;
        c+=texture2D(uImage,uv-stepSize*vec2(0.0,1.0)).rgb*0.04;
        c+=texture2D(uImage,uv+stepSize*vec2(0.707,0.707)).rgb*0.04;
        c+=texture2D(uImage,uv+stepSize*vec2(-0.707,0.707)).rgb*0.04;
        c+=texture2D(uImage,uv+stepSize*vec2(0.707,-0.707)).rgb*0.04;
        c+=texture2D(uImage,uv-stepSize*vec2(0.707,0.707)).rgb*0.04;
        return c;
      }

      void main(){
        vec2 p=(vUv-0.5)*2.0;
        float radius=length(p);
        // The central focal area is untouched. Refraction bends only the outer rim.
        float rim=smoothstep(0.70,1.0,radius);
        vec2 screenUv=vClip.xy/vClip.w*0.5+0.5;
        vec2 imageUv=uLensCenter+(screenUv-uLensCenter)*(1.0+0.075*rim*rim)/1.75;
        float defocus=smoothstep(0.42,0.98,radius);
        vec2 blurStep=uTexel*(6.0*defocus*defocus);
        vec3 imageColor=soften(imageUv,blurStep);
        // The curved retaining edge absorbs a little light, without fogging the center.
        float falloff=smoothstep(0.80,1.0,radius);
        imageColor*=1.0-0.13*falloff;
        gl_FragColor=vec4(imageColor,1.0);
        #include <colorspace_fragment>
      }
    `
  });
  const lensHeight=1.835,lensRadius=.787;
  const opticalDisc=new THREE.Mesh(new THREE.CircleGeometry(lensRadius,128),opticalMaterial);opticalDisc.rotation.x=-Math.PI/2;opticalDisc.position.y=lensHeight;loupe.add(opticalDisc);
  const lens=new THREE.Mesh(new THREE.SphereGeometry(lensRadius,80,24,0,Math.PI*2,0,Math.PI/2),new THREE.MeshPhysicalMaterial({color:0xeaf2ef,metalness:0,roughness:.018,transmission:1,thickness:.025,ior:1.46,transparent:true,opacity:.10,clearcoat:1,clearcoatRoughness:.025,envMapIntensity:.8}));lens.scale.y=.055;lens.position.y=lensHeight+.004;loupe.add(lens);
  const glint=new THREE.Mesh(new THREE.TorusGeometry(.63,.006,6,50,Math.PI*.65),new THREE.MeshBasicMaterial({color:0xecf2e9,transparent:true,opacity:.13}));glint.rotation.x=-Math.PI/2;glint.rotation.z=.6;glint.position.y=lensHeight+.04;loupe.add(glint);
  loupe.scale.set(1.242,.288,1.242);loupe.rotation.set(-.23,0,-.20);

  const shadowCanvas=document.createElement('canvas');shadowCanvas.width=128;shadowCanvas.height=128;const sh=shadowCanvas.getContext('2d');const grad=sh.createRadialGradient(64,64,8,64,64,64);grad.addColorStop(0,'rgba(0,0,0,.7)');grad.addColorStop(1,'rgba(0,0,0,0)');sh.fillStyle=grad;sh.fillRect(0,0,128,128);
  const contact=new THREE.Mesh(new THREE.PlaneGeometry(2.8,2.8),new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(shadowCanvas),transparent:true,depthWrite:false}));contact.rotation.x=-Math.PI/2;contact.position.y=.38;group.add(contact);
  const ray=new THREE.Raycaster(),pointer=new THREE.Vector2(),hit=new THREE.Vector3();
  const apertureOffset=new THREE.Vector3(0,lensHeight,0).multiply(loupe.scale).applyEuler(loupe.rotation);
  const hoverHeight=.72;
  const pointerPlane=new THREE.Plane(new THREE.Vector3(0,1,0),-(hoverHeight+apertureOffset.y));
  const cover=document.getElementById('cover');
  const desired=new THREE.Vector3(0,hoverHeight,0);loupe.position.copy(desired);
  let active=true,paused=reduced.matches,hovering=false,enterStart=0,last=performance.now(),width=0,height=0,mobile=false;
  function resize(){
    const r=canvas.getBoundingClientRect();if(!r.width||!r.height||r.width===width&&r.height===height)return;
    width=r.width;height=r.height;mobile=width<700;
    const viewWidth=clamp(width/60,7.8,28),viewHeight=viewWidth*height/width;
    camera.left=-viewWidth/2;camera.right=viewWidth/2;camera.top=viewHeight/2;camera.bottom=-viewHeight/2;
    camera.position.set(0,20,5);camera.lookAt(0,0,0);camera.updateProjectionMatrix();
    group.rotation.y=0;
    renderer.setSize(width,height,false);
    const pixels=renderer.getDrawingBufferSize(new THREE.Vector2());
    filmViewport.value.copy(pixels);
    opticalTarget.setSize(pixels.x,pixels.y);opticalMaterial.uniforms.uTexel.value.set(1/pixels.x,1/pixels.y);
    document.getElementById('cover-instruction').textContent=mobile?'Touch to inspect. Press to enter.':'Move to inspect. Press to enter.';
  }
  function move(event){
    const cord=document.getElementById('darkroom-switch').getBoundingClientRect();
    const nearCord=event.clientX>cord.left-65&&event.clientX<cord.right+65&&event.clientY>cord.top-35&&event.clientY<cord.bottom+55;
    loupe.visible=!nearCord;contact.visible=!nearCord;
    if(nearCord){document.body.classList.remove('has-loupe');return;}
    if(enterStart||!active||event.target.closest('dialog,button,a'))return;
    const r=canvas.getBoundingClientRect();pointer.set((event.clientX-r.left)/r.width*2-1,-(event.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);
    if(!ray.ray.intersectPlane(pointerPlane,hit))return;
    const p=group.worldToLocal(hit.clone()).sub(apertureOffset);hovering=true;
    desired.set(p.x,hoverHeight,p.z);loupe.position.copy(desired);document.body.classList.add('has-loupe');
    canvas.dataset.inspecting='true';
  }
  cover.addEventListener('pointermove',move,{passive:true});cover.addEventListener('pointerdown',move,{passive:true});
  cover.addEventListener('pointerleave',()=>{hovering=false;loupe.visible=true;contact.visible=true;document.body.classList.remove('has-loupe');desired.set(0,hoverHeight,0)});
  function drawOptics(){
    scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);
    const aperture=loupe.localToWorld(new THREE.Vector3(0,lensHeight,0)).project(camera);
    opticalMaterial.uniforms.uLensCenter.value.set(aperture.x*.5+.5,aperture.y*.5+.5);
    sharpTexture.offset.copy(texture.offset);
    const layerMask=camera.layers.mask;
    filmMaterial.map=sharpTexture;camera.layers.set(1);
    renderer.setRenderTarget(opticalTarget);renderer.render(scene,camera);
    renderer.setRenderTarget(null);camera.layers.mask=layerMask;filmMaterial.map=texture;
  }
  function frame(now){
    requestAnimationFrame(frame);const dt=Math.min((now-last)/1000,.05);last=now;
    if(!active||document.hidden)return;
    resize();
    if(!paused&&!enterStart)texture.offset.x=wrap(texture.offset.x+dt*.003);
    if(!enterStart){
      const target=desired;
      loupe.position.lerp(target,reduced.matches?1:1-Math.exp(-dt*12));
    }else{
      const t=clamp((now-enterStart)/700,0,1),ease=t*t*(3-2*t);
      darkroomLevel.value=ease;
      key.color.setRGB(1,1-.91*ease,1-.97*ease);
      rim.color.setRGB(1,1-.94*ease,1-.98*ease);
    }
    contact.position.x=loupe.position.x;contact.position.z=loupe.position.z;contact.material.opacity=enterStart ? .2 : .65;
    drawOptics(now);renderer.render(scene,camera);
  }
  resize();drawOptics(performance.now());renderer.render(scene,camera);canvas.dataset.ready='true';canvas.dataset.model='reference-silver-loupe-synchronized-film';
  requestAnimationFrame(frame);
  return{
    pause(value){paused=value},setActive(value){active=value},
    enter(){enterStart=performance.now();canvas.dataset.transition='darkroom';document.body.classList.remove('has-loupe')},
    reset(){enterStart=0;loupe.visible=true;contact.visible=true;strip.scale.set(1,1,1);strip.position.x=0;darkroomLevel.value=0;key.color.set(0xf5f5ed);rim.color.set(0xe2e5f3);camera.zoom=1;camera.updateProjectionMatrix();desired.set(0,hoverHeight,0);loupe.position.copy(desired);hovering=false;document.body.classList.remove('has-loupe');canvas.dataset.transition='idle';resize()}
  };
}

