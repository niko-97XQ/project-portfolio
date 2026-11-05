export const frames = [
 {id:'none',name:'No border'}, {id:'paper',name:'Photo paper'},
 {id:'film35',name:'35mm film'}, {id:'film120',name:'120 film'},
 {id:'instant',name:'Instant Square'}, {id:'mini',name:'Instant Mini'},
 {id:'wide',name:'Instant Wide'}
];

// All dimensions scale with the photo, so preview and JPG use the same composition.
const stockDimensions = {
 'polaroid-sx70-film': [88,107,79,79],
 'polaroid-itype': [88,107,79,79],
 'instax-mini-film': [54,86,46,62],
 'instax-square-film': [72,86,62,62],
 'instax-wide-film': [108,86,99,62]
};
export function frameLayout(w,h,id,width=100,paper=null){
 if(id==='none')return {w,h,x:0,y:0,iw:w,ih:h};
 const unit=Math.min(w,h),k=width/100;
 const dimensions = stockDimensions[paper?.stock];
 if (dimensions && paper.frame === id) {
  const [sheetW,sheetH,imageW,imageH] = dimensions;
  const scale = unit / Math.min(imageW,imageH);
  const side = (sheetW-imageW)/2;
  const x = Math.round(side*scale*k), y = x;
  const iw = Math.round(imageW*scale), ih = Math.round(imageH*scale);
  return {x,y,iw,ih,w:iw+2*x,h:ih+y+Math.round((sheetH-imageH-side)*scale*k)};
 }
 let iw=w,ih=h;
 if(id==='instant'){iw=unit;ih=unit;}
 if(id==='mini'){iw=unit;ih=unit*1.32;}
 if(id==='wide'){iw=unit*1.6;ih=unit;}
 const instant=['instant','mini','wide'].includes(id);
 const side=Math.round(unit*(id==='film35'?.115:id==='film120'?.065:.065)*k);
 const top=Math.round(unit*(instant?.065:.065)*k);
 const bottom=Math.round(unit*(instant?.25:.065)*k);
 return {w:Math.round(iw+side*2),h:Math.round(ih+top+bottom),x:side,y:top,iw:Math.round(iw),ih:Math.round(ih)};
}

export function composePrint(target,photo,{id='none',width=100,fit='contain',paper=null}={}){
 const l=frameLayout(photo.width,photo.height,id,width,paper),c=target.getContext('2d');
 if(target.width!==l.w)target.width=l.w;if(target.height!==l.h)target.height=l.h;
 c.clearRect(0,0,l.w,l.h);
 const film=id.startsWith('film');c.fillStyle=film?'#171410':'#f0ede5';c.fillRect(0,0,l.w,l.h);
 if(id!=='none'&&!film){
  const light=c.createLinearGradient(0,0,l.w*.7,l.h);light.addColorStop(0,'#faf8f0');light.addColorStop(.42,'#f0ede3');light.addColorStop(.82,'#e9e5da');light.addColorStop(1,'#ddd8cc');c.fillStyle=light;c.fillRect(0,0,l.w,l.h);
 }
 if(id!=='none'){
  // Fine deterministic paper/acetate grain, only on the physical margin.
  for(let y=0;y<l.h;y+=3)for(let x=0;x<l.w;x+=3){
   if(x>l.x&&x<l.x+l.iw&&y>l.y&&y<l.y+l.ih)continue;
   const n=((Math.imul(x+1,73856093)^Math.imul(y+1,19349663))>>>0)%11;
   c.fillStyle=`rgba(${film?'255,219,160':'57,47,33'},${n/650})`;c.fillRect(x,y,1,1);
  }
 }
 c.save();c.beginPath();c.rect(l.x,l.y,l.iw,l.ih);c.clip();
 const scale=(fit==='cover'?Math.max:Math.min)(l.iw/photo.width,l.ih/photo.height);
 const dw=photo.width*scale,dh=photo.height*scale;
 c.drawImage(photo,l.x+(l.iw-dw)/2,l.y+(l.ih-dh)/2,dw,dh);c.restore();
 if(id!=='none'&&!film){
  const edge=Math.max(1,Math.min(l.w,l.h)*.002),depth=edge*4;
  // A shallow recess at the emulsion edge, baked into the exported print.
  c.save();c.beginPath();c.rect(l.x,l.y,l.iw,l.ih);c.clip();
  const top=c.createLinearGradient(0,l.y,0,l.y+depth);top.addColorStop(0,'#30291e44');top.addColorStop(1,'#30291e00');c.fillStyle=top;c.fillRect(l.x,l.y,l.iw,depth);
  const left=c.createLinearGradient(l.x,0,l.x+depth,0);left.addColorStop(0,'#30291e2c');left.addColorStop(1,'#30291e00');c.fillStyle=left;c.fillRect(l.x,l.y,depth,l.ih);c.restore();
  c.strokeStyle='#ffffff88';c.lineWidth=edge;c.beginPath();c.moveTo(l.x,l.y+l.ih+edge/2);c.lineTo(l.x+l.iw,l.y+l.ih+edge/2);c.lineTo(l.x+l.iw,l.y);c.stroke();
  c.fillStyle='#ffffffb0';c.fillRect(0,0,l.w,edge);c.fillRect(0,0,edge,l.h);
  c.fillStyle='#756d5e55';c.fillRect(0,l.h-edge,l.w,edge);c.fillRect(l.w-edge,0,edge,l.h);
 }
 if(id==='film35'){
  const holeW=l.x*.37,holeH=holeW*1.48,step=holeH*1.8;
  c.fillStyle='#e9e2cd';
  for(let y=l.y+step*.3;y+holeH<l.h-l.y;y+=step){
   for(const x of [l.x*.38,l.w-l.x*.75]){c.beginPath();c.roundRect(x,y,holeW,holeH,holeW*.14);c.fill();}
  }
 }
 if(film){
  // Edge imprints follow the film transport direction, outside the perforations.
  const fontSize=Math.max(1,l.x*(id==='film35'?.30:.42));
  c.save();c.fillStyle='#f4d58a';c.font=`700 ${fontSize}px "Bahnschrift", "Segoe UI", sans-serif`;c.textBaseline='middle';
  const inset=l.x*(id==='film35'?.18:.45);
  c.translate(inset,l.y+l.ih*.16);c.rotate(Math.PI/2);
  c.fillText(id==='film35'?'AMOMENT  35  •  400':'AMOMENT  120',0,0);
  c.fillText('01',l.ih*.58,0);c.restore();
  c.save();c.fillStyle='#f4d58a';c.font=`700 ${fontSize}px "Bahnschrift", "Segoe UI", sans-serif`;c.textBaseline='middle';
  c.translate(l.w-inset,l.y+l.ih*.24);c.rotate(Math.PI/2);
  c.fillText('▷  01',0,0);c.fillText(id==='film35'?'400  •  AMOMENT':'120  •  AMOMENT',l.ih*.42,0);c.restore();
 }
 return l;
}
