import {checkTranslation} from './checks.mjs';
import {checkCompleteness} from './page-completeness.mjs';
const samples=[
  [['Pile diameter 600 mm','桩径 600 mm'],['Minimum concrete cover 75 mm','混凝土最小保护层厚度 75 mm'],['Do not load the pile before testing.','检测前不得对桩施加荷载。']],
  [['Ketebalan plat 12 mm','钢板厚度 12 mm'],['Panjang cerucuk 18 m','桩长 18 m'],['Semak aras sebelum pemasangan.','安装前核对标高。']]
];
const el=id=>document.getElementById(id);
function sample(){return samples[el('page').selectedIndex]}
function row(parent,id,value){const tr=parent.insertRow();tr.insertCell().textContent=id;const c=tr.insertCell();if(typeof value==='string')c.textContent=value;else c.append(value)}
function render(){el('src').replaceChildren();el('dst').replaceChildren();sample().forEach(([source,target],i)=>{const id='A'+(i+1),field=document.createElement('textarea');field.value=target;field.setAttribute('aria-label',id+' 中文译文');field.addEventListener('input',check);row(el('src'),id,source);row(el('dst'),id,field)});check()}
function check(){const inputs=[...el('dst').querySelectorAll('textarea')],unsafe=new Set(),messages=[];
 const rows=sample().map(([source],i)=>{const id='A'+(i+1),target=inputs[i].value;const errors=checkTranslation(source,target);if(errors.some(s=>/数字|单位/.test(s)))unsafe.add(id);for(const error of errors)messages.push(id+'：'+error);return {id,source,target,disposition:'translate'}});
 const {completeness:c}=checkCompleteness(rows,rows.map(r=>({id:r.id,rendered:!!r.target.trim(),flags:[]})),unsafe);
 el('status').textContent='可写入 '+c.written+' / '+c.expected+' 项'+(messages.length?' · 需要修订':' · 规则检查通过');
 el('issues').replaceChildren(...messages.map(m=>{const li=document.createElement('li');li.textContent=m;return li}))}
el('page').onchange=render;el('restore').onclick=render;el('fault').onclick=()=>{const fields=el('dst').querySelectorAll('textarea');fields[0].value=sample()[0][1].replace(/\d+/,'9');fields[1].value='';check()};render();
